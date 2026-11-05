"""cook_events table for tracking when recipes are cooked

A cook event records that a specific recipe was cooked at a specific time.
Each event belongs to one group (tenant) and one recipe. When a recipe is
deleted, all its cook events are removed automatically (cascade).

Revision ID: 0021_cook_events
Revises: 0020_media_kind
Create Date: 2026-10-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0021_cook_events"
down_revision = "0020_media_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check if table already exists (e.g., from create_all)
    conn = op.get_bind()
    table_names = conn.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name='cook_events'")
    ).fetchall()
    if table_names:
        return

    op.create_table(
        "cook_events",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("recipe_id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["recipes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.Index("ix_cook_events_recipe_id", "recipe_id"),
        sa.Index("ix_cook_events_group_id", "group_id"),
    )


def downgrade() -> None:
    op.drop_table("cook_events")
