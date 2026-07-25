"""add recipe_ingredients.ref_recipe_id (link a recipe as a component)

Nullable FK to recipes with ON DELETE SET NULL, indexed. Idempotent: the
metadata baseline (0001, create_all) already builds it on a FRESH database, so
each step is guarded on actual presence — add only what's missing on existing
installs.

Revision ID: 0004_ingredient_ref_recipe
Revises: 0003_recipe_share_token
Create Date: 2026-07-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_ingredient_ref_recipe"
down_revision = "0003_recipe_share_token"
branch_labels = None
depends_on = None

_INDEX = "ix_recipe_ingredients_ref_recipe_id"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("recipe_ingredients")}
    if "ref_recipe_id" not in cols:
        with op.batch_alter_table("recipe_ingredients") as batch:
            batch.add_column(sa.Column("ref_recipe_id", sa.String(length=36), nullable=True))
    indexed = any("ref_recipe_id" in ix.get("column_names", [])
                  for ix in insp.get_indexes("recipe_ingredients"))
    if not indexed:
        op.create_index(_INDEX, "recipe_ingredients", ["ref_recipe_id"])


def downgrade() -> None:
    with op.batch_alter_table("recipe_ingredients") as batch:
        batch.drop_index(_INDEX)
        batch.drop_column("ref_recipe_id")
