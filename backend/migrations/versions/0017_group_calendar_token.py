"""add groups.calendar_token for the public meal-plan iCalendar feed

Nullable + unique, indexed — the feed lookup is a single equality filter on the
token, unauthenticated, and hit on a timer by every subscribed client, so it
must not be a sequential scan over groups. NULLs stay distinct, so households
that have never published a feed coexist happily.

Shaped identically to ``recipes.share_token`` (0003) on purpose: same
String(64), same unique index, same plain-text storage. One capability-token
pattern in this codebase, not two.

Idempotent by design, for the same reason 0003 is: the metadata-driven baseline
(0001) is ``create_all()`` from the live model, so a FRESH database already has
this column and its index by the time this revision runs, while an existing
install does not. Each step is therefore guarded on actual presence and adds
only what is missing.

The downgrade is real: it drops the index and the column. That destroys any
published tokens, which is correct and not recoverable — a downgraded install
has no code to serve the feed, so a surviving token would be a dangling
capability. Subscribers get a 404 and the household re-publishes after a
re-upgrade.

Revision ID: 0017_group_calendar_token
Revises: 0016_hot_fk_indexes
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_group_calendar_token"
down_revision = "0016_hot_fk_indexes"
branch_labels = None
depends_on = None

_INDEX = "ix_groups_calendar_token"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("groups")}
    if "calendar_token" not in cols:
        with op.batch_alter_table("groups") as batch:
            batch.add_column(
                sa.Column("calendar_token", sa.String(length=64), nullable=True)
            )
    # Create the unique index only if nothing already indexes calendar_token
    # (create_all names it ix_groups_calendar_token on a fresh DB).
    indexed = any("calendar_token" in ix.get("column_names", [])
                  for ix in sa.inspect(op.get_bind()).get_indexes("groups"))
    if not indexed:
        op.create_index(_INDEX, "groups", ["calendar_token"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    indexes = {ix["name"] for ix in insp.get_indexes("groups")}
    cols = {c["name"] for c in insp.get_columns("groups")}
    if "calendar_token" not in cols and _INDEX not in indexes:
        return  # already downgraded (idempotent)

    # SQLite cannot DROP COLUMN in place, so batch_alter_table rebuilds the
    # table: create _alembic_tmp_groups, copy, DROP TABLE groups, rename. The
    # app turns foreign keys ON (extensions.py PRAGMA), and `groups` is the
    # parent of nearly every table, so that DROP fails with "FOREIGN KEY
    # constraint failed" the moment a household has any data — an empty
    # database downgrades fine, which is exactly how this hides. Suspend
    # enforcement around the rebuild; PRAGMA is a no-op inside a transaction,
    # hence autocommit_block. Same dance as 0013/0014/0015.
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        with op.get_context().autocommit_block():
            op.execute('DROP TABLE IF EXISTS "_alembic_tmp_groups"')
            op.execute("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("groups") as batch:
            if _INDEX in indexes:
                batch.drop_index(_INDEX)
            if "calendar_token" in cols:
                batch.drop_column("calendar_token")
    finally:
        if sqlite:
            with op.get_context().autocommit_block():
                op.execute("PRAGMA foreign_keys=ON")
