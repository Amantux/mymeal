"""add recipe_videos (how-to videos: external links and uploaded files)

A recipe can carry several videos, ordered. Each row is EITHER a link (url) or
an uploaded file under <DATA_DIR>/videos (filename) — the invariant is enforced
in services/videos.py so a bad request is a 422 rather than an IntegrityError.

Additive: a new table only, nothing existing is touched, so there is no
backfill and no two-step deploy. Idempotent create so a partially-applied run
can be retried.

Revision ID: 0010_recipe_videos
Revises: 0009_recipe_versions
Create Date: 2026-08-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_recipe_videos"
down_revision = "0009_recipe_versions"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("recipe_videos"):
        return
    op.create_table(
        "recipe_videos",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("url", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recipe_id", sa.String(length=36),
                  sa.ForeignKey("recipes.id"), nullable=False),
        sa.Column("group_id", sa.String(length=36),
                  sa.ForeignKey("groups.id"), nullable=True),
    )
    # Both are filtered on in every query: recipe_id to list a recipe's videos,
    # group_id because every query is tenant-scoped.
    op.create_index("ix_recipe_videos_recipe_id", "recipe_videos", ["recipe_id"])
    op.create_index("ix_recipe_videos_group_id", "recipe_videos", ["group_id"])


def downgrade() -> None:
    # Drops the rows with the table. Uploaded files under <DATA_DIR>/videos are
    # deliberately left on disk: a downgrade should not destroy user data that
    # a re-upgrade cannot recover.
    if _has_table("recipe_videos"):
        op.drop_index("ix_recipe_videos_group_id", table_name="recipe_videos")
        op.drop_index("ix_recipe_videos_recipe_id", table_name="recipe_videos")
        op.drop_table("recipe_videos")
