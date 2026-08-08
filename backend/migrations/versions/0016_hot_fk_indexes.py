"""index the hot tenant/FK filter columns, and align unit_conversions timestamps

Two schema-hygiene fixes found in a sweep:

1. Postgres does NOT index a foreign key automatically. `recipes.group_id` is the
   tenant filter on every list/count query, and `recipe_ingredients.recipe_id` /
   `recipe_steps.recipe_id` / `shopping_list_items.shopping_list_id` are what
   every `selectinload` IN-query and every `_repoint`/`delete_unit` update filter
   on — all sequential scans today. Sibling columns (`ref_recipe_id`,
   `recipe_versions.recipe_id`) already carry `index=True`, so the omission looks
   accidental rather than deliberate.

2. 0012 created `unit_conversions.created_at/updated_at` as
   `DateTime(timezone=True)` while `TimestampMixin` declares a bare `DateTime`.
   So a fresh Postgres database (built by `create_all` from the models) gets
   `timestamp without time zone` and one upgraded from <=0011 gets
   `timestamp with time zone` — one codebase, two schemas, which is exactly what
   0013/0015 exist to prevent. Align the migrated shape to the model.

Idempotent in both directions: index creation is skipped when it already exists
(fresh DBs get the indexes from the model metadata), and the type change is
Postgres-only because SQLite has no timezone-aware timestamp type to diverge on.

Revision ID: 0016_hot_fk_indexes
Revises: 0015_food_allergens_not_null
Create Date: 2026-08-08
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_hot_fk_indexes"
down_revision = "0015_food_allergens_not_null"
branch_labels = None
depends_on = None

# (index name, table, column) — named the way SQLAlchemy would name them, so a
# metadata-built database and a migrated one agree.
_INDEXES = [
    ("ix_recipes_group_id", "recipes", "group_id"),
    ("ix_recipe_ingredients_recipe_id", "recipe_ingredients", "recipe_id"),
    ("ix_recipe_steps_recipe_id", "recipe_steps", "recipe_id"),
    ("ix_shopping_list_items_shopping_list_id", "shopping_list_items",
     "shopping_list_id"),
]


def _inspector():
    return sa.inspect(op.get_bind())


def _tables() -> set:
    return set(_inspector().get_table_names())


def _indexes(table) -> set:
    if table not in _tables():
        return set()
    return {ix["name"] for ix in _inspector().get_indexes(table)}


def _columns(table) -> set:
    if table not in _tables():
        return set()
    return {c["name"] for c in _inspector().get_columns(table)}


def upgrade() -> None:
    for name, table, column in _INDEXES:
        if table not in _tables() or column not in _columns(table):
            continue
        if name in _indexes(table):
            continue  # already present (fresh DB from the model metadata)
        op.create_index(name, table, [column])

    # Postgres-only: SQLite stores both as TEXT, so there is nothing to align.
    if op.get_bind().dialect.name == "postgresql":
        if "unit_conversions" in _tables():
            for col in ("created_at", "updated_at"):
                if col in _columns("unit_conversions"):
                    op.alter_column(
                        "unit_conversions", col,
                        type_=sa.DateTime(), existing_nullable=True,
                        postgresql_using=f"{col} AT TIME ZONE 'UTC'")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        if "unit_conversions" in _tables():
            for col in ("created_at", "updated_at"):
                if col in _columns("unit_conversions"):
                    op.alter_column(
                        "unit_conversions", col,
                        type_=sa.DateTime(timezone=True), existing_nullable=True,
                        postgresql_using=f"{col} AT TIME ZONE 'UTC'")

    for name, table, _column in _INDEXES:
        if name in _indexes(table):
            op.drop_index(name, table_name=table)
