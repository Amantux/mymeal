"""unit_conversions: remembered gram-weights for units the density table lacks

The built-in density table covers volume units of common foods. It cannot cover
count units — there is no honest gram value for "a clove" in the abstract — but
"1 stick of butter" has a real, well-known answer. This table remembers the ones
the app looks up, once, with a record of where each came from.

New table only: nothing existing is touched, so this applies and reverses
cleanly. Idempotent both ways so a retry after a mid-run failure is safe.

Revision ID: 0012_unit_conversions
Revises: 0011_recipe_temperature
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_unit_conversions"
down_revision = "0011_recipe_temperature"
branch_labels = None
depends_on = None


def _has_table(name) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("unit_conversions"):
        return
    op.create_table(
        "unit_conversions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("food_term", sa.String(length=120), nullable=False),
        sa.Column("grams_per_unit", sa.Float(), nullable=False),
        # server_default on every added column so create_all() and a migrated
        # database describe the same table.
        sa.Column("source", sa.String(length=16), nullable=False,
                  server_default="web"),
        sa.Column("source_url", sa.String(length=1024), nullable=False,
                  server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="pending"),
        sa.Column("group_id", sa.String(length=36), sa.ForeignKey("groups.id"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        # The lookup upserts on this, so the second recipe using sticks of
        # butter costs nothing. Unique per household, not globally: one
        # household's corrected value must not leak into another's.
        sa.UniqueConstraint("group_id", "unit", "food_term",
                            name="uq_unit_conversion_scope"),
    )
    op.create_index("ix_unit_conversions_unit", "unit_conversions", ["unit"])
    op.create_index("ix_unit_conversions_food_term", "unit_conversions",
                    ["food_term"])
    op.create_index("ix_unit_conversions_status", "unit_conversions", ["status"])
    op.create_index("ix_unit_conversions_group_id", "unit_conversions",
                    ["group_id"])


def downgrade() -> None:
    if _has_table("unit_conversions"):
        # Dropping the table drops its indexes with it on both backends.
        op.drop_table("unit_conversions")
