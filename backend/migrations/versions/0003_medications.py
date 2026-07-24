"""add the medications table

Metadata-driven + idempotent (checkfirst): a fresh DB already has this table
from the metadata baseline (0001), so this skips there; an existing install
stamped at the baseline gets the table created here.

Revision ID: 0003_medications
Revises: 0002_drop_legacy_pantry
Create Date: 2026-07-24
"""
from alembic import op

from app.models.medication import Medication

revision = "0003_medications"
down_revision = "0002_drop_legacy_pantry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Medication.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Medication.__table__.drop(op.get_bind(), checkfirst=True)
