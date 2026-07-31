"""add recipe_versions (edit history + experiments)

Point-in-time JSON snapshots of a recipe's content. `kind="auto"` rows are the
edit-history timeline (captured before each save, pruned to a cap); `kind=
"experiment"` rows are deliberate branches with feedback that promote into the
live recipe. Idempotent: a fresh DB builds the table via create_all, so this
delta only adds it to DBs stamped before the table existed.

Revision ID: 0009_recipe_versions
Revises: 0008_api_token_access
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_recipe_versions"
down_revision = "0008_api_token_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "recipe_versions" in insp.get_table_names():
        return
    op.create_table(
        "recipe_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("recipe_id", sa.String(36),
                  sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.String(36),
                  sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("label", sa.String(255), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
        sa.Column("parent_version_id", sa.String(36), nullable=True),
        sa.Column("snapshot", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(36),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_recipe_versions_recipe_id", "recipe_versions", ["recipe_id"])
    op.create_index("ix_recipe_versions_group_id", "recipe_versions", ["group_id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "recipe_versions" in insp.get_table_names():
        op.drop_table("recipe_versions")
