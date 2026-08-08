"""recipe_ingredients.qualifier, foods.classification, foods.allergens

The variety split off a food name ("Vietnamese" from "Vietnamese cinnamon")
needs somewhere structured to live, and the material-boundary guard needs to
know what KIND of thing a food is and what it contains.

`qualifier` is deliberately NOT `note`: note means preparation ("finely
chopped") and is already contaminated with variety words by two AI prompts, so
merging them would make them indistinguishable forever. See
docs/adr/0001-canonical-foods-and-qualifiers.md.

Add-columns only — nothing existing is altered, so this applies and reverses
cleanly and needs no two-step deploy. `server_default` on the string columns so
`create_all()` and a migrated database describe the same table. Idempotent in
both directions.

Revision ID: 0013_food_qualifier
Revises: 0012_unit_conversions
Create Date: 2026-08-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_food_qualifier"
down_revision = "0012_unit_conversions"
branch_labels = None
depends_on = None

_ADDED = {
    "recipe_ingredients": [
        ("qualifier", sa.Column("qualifier", sa.String(length=120),
                                nullable=False, server_default="")),
    ],
    "foods": [
        ("classification", sa.Column("classification", sa.String(length=64),
                                     nullable=False, server_default="")),
        # JSON rather than a delimited string: allergens is a set, and a
        # delimited column is how Food.aliases ended up with three different
        # parsers. SQLite stores JSON as TEXT; Postgres uses its JSON type.
        ("allergens", sa.Column("allergens", sa.JSON(), nullable=True)),
    ],
}


def _has_column(table, column) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {
        c["name"] for c in insp.get_columns(table)
    }


def upgrade() -> None:
    for table, columns in _ADDED.items():
        for name, column in columns:
            if not _has_column(table, name):
                op.add_column(table, column)


def downgrade() -> None:
    # Dropping a column is a table rebuild on SQLite; batch_alter does that
    # transparently and is a plain ALTER on Postgres. The hazard is a FK pointing
    # at the TABLE being rebuilt (not the column): `foods` is FK-referenced by
    # recipe_ingredients and shopping_list_items, and this app enforces foreign
    # keys (extensions.py: PRAGMA foreign_keys=ON), so the rebuild's `DROP TABLE
    # foods` raised "FOREIGN KEY constraint failed" on a populated DB and left an
    # _alembic_tmp corpse. Suspend enforcement around the rebuild (PRAGMA is a
    # no-op inside a transaction → autocommit_block). Same dance as 0014/0015.
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        with op.get_context().autocommit_block():
            for table in _ADDED:
                op.execute(f'DROP TABLE IF EXISTS "_alembic_tmp_{table}"')
            op.execute("PRAGMA foreign_keys=OFF")
    try:
        for table, columns in _ADDED.items():
            present = [name for name, _ in columns if _has_column(table, name)]
            if not present:
                continue
            with op.batch_alter_table(table) as batch:
                for name in present:
                    batch.drop_column(name)
    finally:
        if is_sqlite:
            with op.get_context().autocommit_block():
                op.execute("PRAGMA foreign_keys=ON")
