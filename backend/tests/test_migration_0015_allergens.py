"""Migration 0015: foods.allergens NULL -> '[]', then NOT NULL + default.

The divergence being healed: the model says NOT NULL while 0013 added the
column nullable with no server default, so a fresh create_all database and a
0013-migrated one disagreed about whether an INSERT may omit the column. Hit
twice in one day as "NOT NULL constraint failed: foods.allergens".
"""
import sqlite3

from test_migration_0014_aliases import _insert, _run_alembic


def _db_with_null_allergens(tmp_path):
    """A 0013-era database whose foods hold NULL allergens — the state a
    pre-0013 install lands in after 0013 adds the nullable column."""
    db = str(tmp_path / "a.db")
    assert _run_alembic(db, "upgrade", "0013_food_qualifier").returncode == 0
    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys=ON")
    _insert(c, "groups", {"id": "g", "name": "G"})
    _insert(c, "foods", {"id": "f1", "group_id": "g", "name": "milk"})
    _insert(c, "recipes", {"id": "r", "group_id": "g", "name": "R", "slug": "r"})
    _insert(c, "recipe_ingredients",
            {"id": "i0", "recipe_id": "r", "food_id": "f1",
             "display": "x", "position": 0})
    # Force the NULL regardless of which baseline shape built the table.
    c.execute("PRAGMA ignore_check_constraints=ON")
    try:
        c.execute("UPDATE foods SET allergens = NULL")
        forced = True
    except sqlite3.IntegrityError:
        forced = False  # metadata-built schema already enforces NOT NULL
    c.commit()
    c.close()
    return db, forced


def test_upgrade_backfills_nulls_and_enforces_not_null(tmp_path):
    db, forced = _db_with_null_allergens(tmp_path)

    r = _run_alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-600:]

    c = sqlite3.connect(db)
    nulls = c.execute(
        "SELECT COUNT(*) FROM foods WHERE allergens IS NULL").fetchone()[0]
    assert nulls == 0
    notnull = {r[1]: r[3] for r in c.execute("PRAGMA table_info(foods)")}
    assert notnull["allergens"] == 1, "column is still nullable"
    # The FK rows survived the rebuild (same hazard as 0014).
    assert c.execute("SELECT COUNT(*) FROM recipe_ingredients").fetchone()[0] == 1
    c.close()


def test_a_raw_insert_omitting_allergens_now_works_on_both_paths(tmp_path):
    """The user-visible symptom: whether this INSERT succeeds depended on how
    the database was born. After 0015 the server default answers for it."""
    db, _ = _db_with_null_allergens(tmp_path)
    assert _run_alembic(db, "upgrade", "head").returncode == 0

    c = sqlite3.connect(db)
    # _insert supplies filler only for NOT-NULL-NO-DEFAULT columns; with the
    # server default in place it omits allergens and the default must answer —
    # with '[]', never NULL.
    _insert(c, "foods", {"id": "f2", "group_id": "g", "name": "butter"})
    stored = dict(c.execute("SELECT id, allergens FROM foods"))
    assert stored["f2"] == "[]"
    c.close()


def test_downgrade_returns_to_the_0013_shape(tmp_path):
    db, _ = _db_with_null_allergens(tmp_path)
    assert _run_alembic(db, "upgrade", "head").returncode == 0

    r = _run_alembic(db, "downgrade", "0014_food_aliases_json")
    assert r.returncode == 0, r.stderr[-600:]

    c = sqlite3.connect(db)
    notnull = {r[1]: r[3] for r in c.execute("PRAGMA table_info(foods)")}
    assert notnull["allergens"] == 0, "column should be nullable again"
    # Values stay '[]' — NULLs are not resurrected, by design.
    assert c.execute("SELECT COUNT(*) FROM foods WHERE allergens IS NULL"
                     ).fetchone()[0] == 0
    c.close()

    assert _run_alembic(db, "upgrade", "head").returncode == 0
