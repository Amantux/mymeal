"""add recipes.cook_temperature_c, and backfill it from existing step text

Nothing ever extracted an oven temperature: schema.org has no field for it, so
a scraped recipe only states it inside the instructions.

The backfill is the point. Recipes already imported are missing their
temperature NOW, and a forward-only change would leave every one of them that
way — the complaint was about recipes that have already been scraped. It reads
each recipe's steps with the same parser the importer uses.

Idempotent in both directions: the column add is guarded, and the backfill only
touches rows where the column is still NULL, so a retry after a mid-run failure
cannot double-apply or overwrite a value a user has since corrected by hand.

Revision ID: 0011_recipe_temperature
Revises: 0010_recipe_videos
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_recipe_temperature"
down_revision = "0010_recipe_videos"
branch_labels = None
depends_on = None


def _has_column(table, column) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {
        c["name"] for c in insp.get_columns(table)
    }


def upgrade() -> None:
    if not _has_column("recipes", "cook_temperature_c"):
        op.add_column("recipes", sa.Column("cook_temperature_c", sa.Float(),
                                           nullable=True))
    _backfill()


def _backfill() -> None:
    """Read a temperature out of each recipe's existing steps.

    Imports the app's parser rather than re-implementing the regex here: a
    second copy would drift from the one the importer uses, and then old and
    new recipes would disagree about the same sentence.
    """
    from app.services.cooking import parse_temperature

    bind = op.get_bind()
    recipes = bind.execute(sa.text(
        "SELECT id FROM recipes WHERE cook_temperature_c IS NULL"
    )).fetchall()
    for (recipe_id,) in recipes:
        rows = bind.execute(
            sa.text("SELECT text FROM recipe_steps WHERE recipe_id = :rid "
                    "ORDER BY position"),
            {"rid": recipe_id},
        ).fetchall()
        celsius = parse_temperature(" ".join(r[0] or "" for r in rows))
        if celsius is not None:
            bind.execute(
                sa.text("UPDATE recipes SET cook_temperature_c = :c WHERE id = :rid"),
                {"c": celsius, "rid": recipe_id},
            )


def downgrade() -> None:
    if _has_column("recipes", "cook_temperature_c"):
        op.drop_column("recipes", "cook_temperature_c")
