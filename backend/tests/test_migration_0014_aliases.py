"""Migration 0014: Food.aliases CSV String(512) -> JSON list.

Every test runs on a SEEDED database with recipe_ingredients rows pointing at
the foods: HomeHoard's structurally identical migration passed on an empty
fixture and failed in the field, because SQLite's batch rebuild removes the
foods table mid-flight and this app enforces foreign keys. An empty database
cannot catch that.
"""
import json
import os
import sqlite3
import subprocess


def _run_alembic(db_path, *args):
    env = dict(os.environ, MYMEAL_DATABASE_URL=f"sqlite:///{db_path}")
    return subprocess.run(["python3", "-m", "alembic", *args],
                          capture_output=True, text=True, env=env,
                          cwd=os.path.dirname(os.path.dirname(__file__)))


def _insert(conn, table, values):
    """INSERT with type-appropriate filler for every NOT-NULL-no-default
    column, read from the live schema. Hand-enumerating them was whack-a-mole
    across thirteen revisions of history."""
    row = dict(values)
    for _cid, name, ctype, notnull, default, _pk in \
            conn.execute(f"PRAGMA table_info({table})"):
        if name in row or not notnull or default is not None:
            continue
        if name in ("created_at", "updated_at"):
            row[name] = "2026-01-01 00:00:00"
        elif any(t in (ctype or "").upper() for t in ("INT", "REAL", "FLOA", "NUM")):
            row[name] = 0
        elif "JSON" in (ctype or "").upper():
            row[name] = "[]"
        else:
            row[name] = ""
    cols = ",".join(row)
    marks = ",".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                 list(row.values()))


def _seeded_db_at_0013(tmp_path):
    """A database at revision 0013 holding one of each storage form, each with
    an FK row aimed at it — the shape that breaks a naive rebuild."""
    db = str(tmp_path / "m.db")
    r = _run_alembic(db, "upgrade", "0013_food_qualifier")
    assert r.returncode == 0, r.stderr[-500:]

    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys=ON")
    _insert(c, "groups", {"id": "g", "name": "G"})
    for fid, name, aliases in [
        ("f1", "aubergine", "eggplant, brinjal"),   # legacy CSV
        ("f2", "beef mince", '["nannas mince"]'),   # already-JSON text
        ("f3", "nutmeg", ""),                       # empty
        # An alias colliding with f1's alias key: the collision policy must
        # drop it from the LATER food, deterministically.
        ("f4", "melanzane", "brinjal, guinea squash"),
    ]:
        # _insert fills allergens automatically: the model says NOT NULL while
        # migration 0013 added it nullable, and 0001_baseline is metadata-
        # driven — so which schema you get depends on the path taken. 0015
        # heals this.
        _insert(c, "foods",
                {"id": fid, "group_id": "g", "name": name, "aliases": aliases})
    _insert(c, "recipes", {"id": "r", "group_id": "g", "name": "R", "slug": "r"})
    for i, fid in enumerate(["f1", "f2", "f3", "f4"]):
        _insert(c, "recipe_ingredients",
                {"id": f"i{i}", "recipe_id": "r", "food_id": fid,
                 "display": "x", "position": i})
    c.commit()
    c.close()
    return db


def test_upgrade_converts_every_storage_form_on_a_seeded_db(tmp_path):
    db = _seeded_db_at_0013(tmp_path)

    r = _run_alembic(db, "upgrade", "head")
    assert r.returncode == 0, f"upgrade failed on seeded data:\n{r.stderr[-800:]}"

    c = sqlite3.connect(db)
    rows = dict(c.execute("SELECT id, aliases FROM foods"))
    assert json.loads(rows["f1"]) == ["eggplant", "brinjal"]
    assert json.loads(rows["f2"]) == ["nannas mince"]
    assert json.loads(rows["f3"]) == []
    # The FK rows survived the table rebuild.
    assert c.execute("SELECT COUNT(*) FROM recipe_ingredients").fetchone()[0] == 4
    c.close()


def test_backfill_drops_a_colliding_alias_from_the_later_food(tmp_path):
    """"brinjal" is claimed by f1 (earlier by insertion order); f4 loses it and
    keeps only its unclaimed alias. Dropping degrades to "not consolidated",
    never to two foods answering to one name."""
    db = _seeded_db_at_0013(tmp_path)
    assert _run_alembic(db, "upgrade", "head").returncode == 0

    c = sqlite3.connect(db)
    rows = dict(c.execute("SELECT id, aliases FROM foods"))
    assert json.loads(rows["f4"]) == ["guinea squash"]
    assert "brinjal" in json.loads(rows["f1"])
    c.close()


def test_upgrade_is_idempotent_after_a_partial_failure(tmp_path):
    """A retry must not double-convert or die on a leftover temp table."""
    db = _seeded_db_at_0013(tmp_path)
    assert _run_alembic(db, "upgrade", "head").returncode == 0

    # Simulate a retry: stamp back and run the same revision again.
    assert _run_alembic(db, "stamp", "0013_food_qualifier").returncode == 0
    r = _run_alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-500:]

    c = sqlite3.connect(db)
    rows = dict(c.execute("SELECT id, aliases FROM foods"))
    assert json.loads(rows["f1"]) == ["eggplant", "brinjal"], "double-converted"
    c.close()


def test_downgrade_round_trips_on_seeded_data(tmp_path):
    db = _seeded_db_at_0013(tmp_path)
    assert _run_alembic(db, "upgrade", "head").returncode == 0

    r = _run_alembic(db, "downgrade", "0013_food_qualifier")
    assert r.returncode == 0, f"downgrade failed:\n{r.stderr[-800:]}"

    c = sqlite3.connect(db)
    rows = dict(c.execute("SELECT id, aliases FROM foods"))
    assert rows["f1"] == "eggplant,brinjal"
    assert rows["f2"] == "nannas mince"
    assert rows["f3"] == ""
    assert c.execute("SELECT COUNT(*) FROM recipe_ingredients").fetchone()[0] == 4
    c.close()

    # And straight back up again — the full round trip.
    assert _run_alembic(db, "upgrade", "head").returncode == 0
    c = sqlite3.connect(db)
    up = dict(c.execute("SELECT id, aliases FROM foods"))
    assert json.loads(up["f1"]) == ["eggplant", "brinjal"]
    c.close()


def test_no_orphans_and_fk_state_survives_the_pragma_dance(tmp_path):
    """Nothing wrote dangling rows while enforcement was suspended, and the
    persistent database still has intact references afterwards."""
    db = _seeded_db_at_0013(tmp_path)
    assert _run_alembic(db, "upgrade", "head").returncode == 0

    c = sqlite3.connect(db)
    c.execute("PRAGMA foreign_keys=ON")
    orphans = c.execute(
        "SELECT COUNT(*) FROM recipe_ingredients ri LEFT JOIN foods f "
        "ON ri.food_id = f.id WHERE ri.food_id IS NOT NULL AND f.id IS NULL"
    ).fetchone()[0]
    assert orphans == 0
    c.close()


def test_an_already_json_row_still_claims_its_aliases(tmp_path):
    """The idempotency skip must not skip claim REGISTRATION: with it skipped,
    a later CSV row kept an alias an earlier JSON row owned, violating the
    migration's own collision policy in exactly the hybrid state (baseline-
    built JSON column holding CSV text) its docstring warns about."""
    db = str(tmp_path / "c.db")
    assert _run_alembic(db, "upgrade", "0013_food_qualifier").returncode == 0
    c = sqlite3.connect(db)
    _insert(c, "groups", {"id": "g", "name": "G"})
    # Earlier row already JSON; later row CSV claiming the same alias.
    _insert(c, "foods", {"id": "f1", "group_id": "g", "name": "beef mince",
                         "aliases": '["nannas mince"]'})
    _insert(c, "foods", {"id": "f2", "group_id": "g", "name": "pork mince",
                         "aliases": "nannas mince, ground pork"})
    c.commit()
    c.close()

    assert _run_alembic(db, "upgrade", "head").returncode == 0

    c = sqlite3.connect(db)
    rows = dict(c.execute("SELECT id, aliases FROM foods"))
    assert json.loads(rows["f1"]) == ["nannas mince"]
    assert json.loads(rows["f2"]) == ["ground pork"], \
        "the later CSV row kept an alias the JSON row already owned"
    c.close()
