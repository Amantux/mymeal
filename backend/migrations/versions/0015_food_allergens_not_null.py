"""foods.allergens: NULL -> '[]', then NOT NULL with a server default

The model declares ``allergens: Mapped[list] = mapped_column(JSON,
default=list)`` — non-Optional, so NOT NULL — while migration 0013 added the
column ``nullable=True`` with no server default. Two different schemas from
one codebase, depending on the path taken: 0001_baseline is metadata-driven,
so a fresh database enforces NOT NULL and rejects a raw INSERT omitting the
column, while a database migrated through 0013 accepts it (and can hold
NULLs). This bit twice in one day, both times as
"NOT NULL constraint failed: foods.allergens" in fixtures that were valid
against the other schema.

0013's own docstring states the rule this column broke: server_default so
``create_all()`` and a migrated database describe the same table (its sibling
``classification`` got one; ``allergens`` did not).

Backfill first (NULL becomes '[]'), then the constraint — a NOT NULL cannot
be applied over existing NULLs. Same PRAGMA dance as 0014 and for the same
reason: on SQLite the batch alter rebuilds ``foods``, which two tables
FK-reference under enforced foreign keys, and PRAGMA foreign_keys is a no-op
inside a transaction so it must run in an autocommit block.

Downgrade restores 0013's declared shape (nullable, no default). It does not
resurrect NULLs — there is no record of which rows were NULL, and '[]' means
the same thing everywhere the value is read.

Revision ID: 0015_food_allergens_not_null
Revises: 0014_food_aliases_json
Create Date: 2026-08-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_food_allergens_not_null"
down_revision = "0014_food_aliases_json"
branch_labels = None
depends_on = None


def _column(table, name):
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return None
    for c in insp.get_columns(table):
        if c["name"] == name:
            return c
    return None


def _alter(not_null: bool) -> None:
    col = _column("foods", "allergens")
    if col is None or bool(col.get("nullable")) != not_null:
        return  # already in the target shape (idempotent)

    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        with op.get_context().autocommit_block():
            op.execute('DROP TABLE IF EXISTS "_alembic_tmp_foods"')
            op.execute("PRAGMA foreign_keys=OFF")
    else:
        # Same Postgres rule 0014 hit: an existing DEFAULT that cannot be
        # auto-cast blocks ALTER; drop it and let the alter reinstate one.
        op.execute("ALTER TABLE foods ALTER COLUMN allergens DROP DEFAULT")
    try:
        with op.batch_alter_table("foods") as batch:
            batch.alter_column(
                "allergens",
                existing_type=sa.JSON(),
                nullable=not not_null,
                server_default=sa.text("'[]'") if not_null else None,
            )
    finally:
        if sqlite:
            with op.get_context().autocommit_block():
                op.execute("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    op.execute("UPDATE foods SET allergens = '[]' WHERE allergens IS NULL")
    _alter(not_null=True)


def downgrade() -> None:
    _alter(not_null=False)
