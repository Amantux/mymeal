"""drop the legacy pantry_items table

myMeal no longer owns a pantry — inventory is owned by the companion Edibl app.
Older installs may still have the table; drop it. One-way and idempotent
(``IF EXISTS``): a no-op on fresh databases. This preserves the cleanup the old
hand-rolled _migrate() did, now as a proper migration layered on the baseline.

Revision ID: 0002_drop_legacy_pantry
Revises: 0001_baseline
Create Date: 2026-07-24
"""
from alembic import op

revision = "0002_drop_legacy_pantry"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pantry_items")


def downgrade() -> None:
    pass  # one-way: the pantry now lives in Edibl
