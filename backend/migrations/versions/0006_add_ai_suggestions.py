"""add ai_suggestions (categorize/cluster review queue + ICL examples)

Idempotent create: the 0001 baseline builds this table via create_all on fresh DBs,
so this delta only adds it to DBs stamped before the model existed.

Revision ID: 0006_add_ai_suggestions
Revises: 0005_add_jobs
Create Date: 2026-07-27
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_add_ai_suggestions"
down_revision = "0005_add_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "ai_suggestions" in insp.get_table_names():
        return
    op.create_table(
        "ai_suggestions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("recipe_id", sa.String(36),
                  sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ai_suggestions_kind", "ai_suggestions", ["kind"])
    op.create_index("ix_ai_suggestions_status", "ai_suggestions", ["status"])
    op.create_index("ix_ai_suggestions_recipe_id", "ai_suggestions", ["recipe_id"])
    op.create_index("ix_ai_suggestions_group_id", "ai_suggestions", ["group_id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "ai_suggestions" in insp.get_table_names():
        op.drop_table("ai_suggestions")
