"""0016 adds the hot FK/tenant indexes, idempotently and reversibly.

Postgres does not index a foreign key automatically, so recipes.group_id and the
recipe_id / shopping_list_id columns every selectinload IN-query filters on were
sequential scans. The model now declares index=True too, so a create_all
database and a migrated one describe the same schema — the divergence 0013/0015
exist to prevent.
"""
import os
import sqlite3
import subprocess

_EXPECTED = {
    "recipes": "ix_recipes_group_id",
    "recipe_ingredients": "ix_recipe_ingredients_recipe_id",
    "recipe_steps": "ix_recipe_steps_recipe_id",
    "shopping_list_items": "ix_shopping_list_items_shopping_list_id",
}


def _alembic(db, *args):
    env = dict(os.environ, MYMEAL_DATABASE_URL=f"sqlite:///{db}")
    return subprocess.run(["python3", "-m", "alembic", *args],
                          capture_output=True, text=True, env=env,
                          cwd=os.path.dirname(os.path.dirname(__file__)))


def _indexes(db, table):
    c = sqlite3.connect(db)
    try:
        return {r[1] for r in c.execute(f"PRAGMA index_list({table})")}
    finally:
        c.close()


def test_upgrade_creates_every_hot_index(tmp_path):
    db = str(tmp_path / "m.db")
    r = _alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-800:]
    for table, index in _EXPECTED.items():
        assert index in _indexes(db, table), f"{index} missing on {table}"


def test_downgrade_then_upgrade_round_trips(tmp_path):
    db = str(tmp_path / "m.db")
    assert _alembic(db, "upgrade", "head").returncode == 0
    down = _alembic(db, "downgrade", "0015_food_allergens_not_null")
    assert down.returncode == 0, down.stderr[-800:]
    for table, index in _EXPECTED.items():
        assert index not in _indexes(db, table), f"{index} survived downgrade"
    up = _alembic(db, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-800:]
    for table, index in _EXPECTED.items():
        assert index in _indexes(db, table)


def test_model_metadata_declares_the_same_indexes():
    """The invariant that matters: a create_all database and a migrated one must
    describe the SAME schema. Asserted against the model metadata (the source
    create_all builds from), so a future index added in a migration but not the
    model — or vice versa — fails here."""
    from app.models import Recipe, RecipeIngredient, RecipeStep, ShoppingListItem

    declared = set()
    for model in (Recipe, RecipeIngredient, RecipeStep, ShoppingListItem):
        declared |= {ix.name for ix in model.__table__.indexes}

    for index in _EXPECTED.values():
        assert index in declared, f"model metadata is missing {index}"
