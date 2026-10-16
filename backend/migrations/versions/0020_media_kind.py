"""recipe_videos.kind — photos and videos share the attachment table

A recipe's media slot accepts both images and videos. The two live in one table
because they are the same shape (a link or an upload, ordered, owned by one
recipe) and the UI renders them in one grid; a parallel `recipe_images` table
would duplicate the tenancy, ordering and safe-serving logic that has already
been got right once.

`kind` is PERSISTED rather than derived from the filename or URL. For an upload
the extension would answer it, but for a link nothing does:
`https://youtu.be/xyz` and `https://i.example.com/abc` both carry no extension
and mean different things. A rule that works for half the rows is worse than a
column — derived state that can be wrong is how an image ends up rendered in a
`<video>` element.

The table is NOT renamed to `recipe_media`. A rename is a table rebuild on
SQLite (the deployment that matters: `/data` is the add-on's only durable path),
it invalidates the idempotency guards in 0010, and it buys nothing a user or an
API client can see — the wire surface is `/recipes/<id>/media`, which this
revision's endpoints provide over the existing table.

Existing rows are all videos, which is exactly what `server_default="video"`
gives them — no backfill pass, and no NULL window under NOT NULL.

Add-column only, so it applies and reverses cleanly and needs no two-step
deploy. `server_default` (not just a Python default) so `create_all()` and a
migrated database describe the same table. Idempotent in both directions.

Revision ID: 0020_media_kind
Revises: 0019_ingredient_measures
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0020_media_kind"
down_revision = "0019_ingredient_measures"
branch_labels = None
depends_on = None

_TABLE = "recipe_videos"
_COLUMN = "kind"


def _has_column(table, column) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {
        c["name"] for c in insp.get_columns(table)
    }


def upgrade() -> None:
    if not _has_column(_TABLE, _COLUMN):
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.String(length=16), nullable=False,
                      server_default="video"),
        )


def downgrade() -> None:
    # Dropping the column loses the image/video distinction, so any image rows
    # would come back as videos on a re-upgrade. That is accepted rather than
    # guarded: a downgrade is an operator rolling back to an app version that
    # has no concept of recipe images, and the files themselves survive under
    # <DATA_DIR>/videos either way.
    #
    # Dropping a column is a table rebuild on SQLite; batch_alter does that
    # transparently and is a plain ALTER on Postgres. Nothing FK-references
    # recipe_videos, so the rebuild's DROP TABLE cannot trip a parent
    # constraint. Still clear any corpse left by an interrupted earlier run.
    if not _has_column(_TABLE, _COLUMN):
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.get_context().autocommit_block():
            op.execute(f'DROP TABLE IF EXISTS "_alembic_tmp_{_TABLE}"')
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
