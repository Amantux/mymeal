"""Migration 0019: alias-spelled Unit rows fold into their canonical sibling.

Seeded, and seeded with ingredients POINTING AT the units — an empty fixture
would pass with a migration that deletes the alias rows and strands every
recipe that used them.
"""
import sqlite3

from migration_harness import _insert, _run_alembic  # noqa: E402


def _seeded_db_at_0018(tmp_path):
    """Two households at 0018, holding the unit rows the old canonical_unit
    produced: 'gram' beside 'g', 'ounce' alone, and a household's own 'batch'.
    """
    db = str(tmp_path / "m.db")
    r = _run_alembic(db, "upgrade", "0018_ingredient_free_text")
    assert r.returncode == 0, r.stderr[-500:]

    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys=ON")
    for gid in ("g1", "g2"):
        _insert(c, "groups", {"id": gid, "name": gid})
        _insert(c, "recipes",
                {"id": f"r_{gid}", "group_id": gid, "name": "R", "slug": f"r-{gid}"})
    for uid, gid, name in [
        ("u_g", "g1", "g"),            # already canonical
        ("u_gram", "g1", "gram"),      # alias WITH a canonical sibling
        ("u_grams", "g1", "grams"),    # a second alias of the same unit
        ("u_ounce", "g1", "ounce"),    # alias with NO canonical sibling
        ("u_batch", "g1", "batch"),    # the household's own unit — not ours
        ("u_gram2", "g2", "gram"),     # another household: must not cross over
    ]:
        _insert(c, "units", {"id": uid, "group_id": gid, "name": name})
    for i, (uid, gid) in enumerate([
            ("u_g", "g1"), ("u_gram", "g1"), ("u_grams", "g1"),
            ("u_ounce", "g1"), ("u_batch", "g1"), ("u_gram2", "g2")]):
        _insert(c, "recipe_ingredients",
                {"id": f"i{i}", "recipe_id": f"r_{gid}", "unit_id": uid,
                 "display": "x", "position": i})
    c.commit()
    c.close()
    return db


def _units(db):
    c = sqlite3.connect(db)
    rows = {(g, n) for g, n in c.execute("SELECT group_id, name FROM units")}
    c.close()
    return rows


def test_upgrade_folds_every_alias_spelling_into_one_unit(tmp_path):
    db = _seeded_db_at_0018(tmp_path)

    r = _run_alembic(db, "upgrade", "0019_ingredient_measures")
    assert r.returncode == 0, f"upgrade failed on seeded data:\n{r.stderr[-800:]}"

    # 'gram' and 'grams' folded into the existing 'g'; 'ounce' had no sibling
    # so it was renamed in place; 'batch' is the household's own and untouched.
    assert _units(db) == {("g1", "g"), ("g1", "oz"), ("g1", "batch"),
                          ("g2", "g")}


def test_upgrade_repoints_the_ingredients_of_a_folded_unit(tmp_path):
    """The failure an empty fixture cannot see: deleting the alias row without
    moving its ingredients strands every recipe that used it."""
    db = _seeded_db_at_0018(tmp_path)
    assert _run_alembic(db, "upgrade", "0019_ingredient_measures").returncode == 0

    c = sqlite3.connect(db)
    # Every ingredient still points at a unit that exists...
    orphans = c.execute(
        "SELECT COUNT(*) FROM recipe_ingredients i LEFT JOIN units u "
        "ON i.unit_id = u.id WHERE i.unit_id IS NOT NULL AND u.id IS NULL"
    ).fetchone()[0]
    assert orphans == 0
    # ...and the three g1 gram rows now share ONE unit, which is what makes the
    # shopping list buy the parmesan once.
    shared = c.execute(
        "SELECT COUNT(DISTINCT i.unit_id) FROM recipe_ingredients i "
        "WHERE i.id IN ('i0', 'i1', 'i2')").fetchone()[0]
    assert shared == 1
    c.close()


def test_a_unit_is_never_folded_across_households(tmp_path):
    """Units are group-scoped. g2's 'gram' must become g2's own 'g', never
    g1's."""
    db = _seeded_db_at_0018(tmp_path)
    assert _run_alembic(db, "upgrade", "0019_ingredient_measures").returncode == 0

    c = sqlite3.connect(db)
    gid = c.execute(
        "SELECT u.group_id FROM recipe_ingredients i JOIN units u "
        "ON i.unit_id = u.id WHERE i.id = 'i5'").fetchone()[0]
    c.close()
    assert gid == "g2"


def test_upgrade_is_idempotent(tmp_path):
    """Migrations are retried after a mid-run failure, so a second pass must
    find every name already canonical and change nothing."""
    db = _seeded_db_at_0018(tmp_path)
    assert _run_alembic(db, "upgrade", "0019_ingredient_measures").returncode == 0
    after_first = _units(db)

    assert _run_alembic(db, "downgrade", "0018_ingredient_free_text").returncode == 0
    assert _run_alembic(db, "upgrade", "0019_ingredient_measures").returncode == 0

    assert _units(db) == after_first
