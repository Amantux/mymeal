"""recipe_ingredients.free_text — the author-declared prose lane

A free-text ingredient ("a good knob of butter, for finishing") must never be
find-or-created into the shared Food/Unit catalogs: the whole line becomes a
catalog row and then appears in every ingredient autocomplete in the app.

The flag is PERSISTED rather than derived from `food_id IS NULL` because
emptiness is already taken. An archive import or the paste parser leaves
food_id NULL on any line it could not structure, and the editor deliberately
re-parses exactly those to offer tidy qty·unit·food rows. Without a column the
two states are indistinguishable, so the author's explicit choice gets re-parsed
back into structure on the next edit — the round-trip loss this lane exists to
prevent.

Add-column only, so it applies and reverses cleanly and needs no two-step
deploy. `server_default` (not just a Python default) so `create_all()` and a
migrated database describe the same table, and so existing rows are false rather
than NULL under NOT NULL. Idempotent in both directions.

Revision ID: 0017_ingredient_free_text
Revises: 0016_hot_fk_indexes
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_ingredient_free_text"
down_revision = "0016_hot_fk_indexes"
branch_labels = None
depends_on = None

_TABLE = "recipe_ingredients"
_COLUMN = "free_text"


def _has_column(table, column) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {
        c["name"] for c in insp.get_columns(table)
    }


def upgrade() -> None:
    if not _has_column(_TABLE, _COLUMN):
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.Boolean(), nullable=False,
                      server_default=sa.false()),
        )


def downgrade() -> None:
    # Dropping a column is a table rebuild on SQLite; batch_alter does that
    # transparently and is a plain ALTER on Postgres. recipe_ingredients holds
    # FKs *to* other tables but nothing FK-references it, so unlike 0013 (which
    # rebuilt `foods`) there is no enforcement to suspend — the rebuild's
    # DROP TABLE cannot trip a parent constraint. Still clear any corpse left by
    # an interrupted earlier run.
    if not _has_column(_TABLE, _COLUMN):
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.get_context().autocommit_block():
            op.execute(f'DROP TABLE IF EXISTS "_alembic_tmp_{_TABLE}"')
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
