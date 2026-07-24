"""add recipes.share_token for public share links

Nullable + unique, indexed (the public lookup is by token). NULLs stay distinct
so unshared recipes coexist.

Idempotent by design: the metadata-driven baseline (0001) is ``create_all()``
from the live model, so a FRESH database already has this column and its index
by the time this revision runs. Existing pre-share installs do NOT. So each step
is guarded on actual presence — add only what's missing.

Revision ID: 0003_recipe_share_token
Revises: 0002_drop_legacy_pantry
Create Date: 2026-07-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_recipe_share_token"
down_revision = "0002_drop_legacy_pantry"
branch_labels = None
depends_on = None

_INDEX = "ix_recipes_share_token"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("recipes")}
    if "share_token" not in cols:
        with op.batch_alter_table("recipes") as batch:
            batch.add_column(sa.Column("share_token", sa.String(length=64), nullable=True))
    # Create the unique index only if nothing already indexes share_token
    # (create_all names it ix_recipes_share_token on a fresh DB).
    indexed = any("share_token" in ix.get("column_names", [])
                  for ix in insp.get_indexes("recipes"))
    if not indexed:
        op.create_index(_INDEX, "recipes", ["share_token"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("recipes") as batch:
        batch.drop_index(_INDEX)
        batch.drop_column("share_token")
