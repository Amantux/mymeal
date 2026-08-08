"""Migration 0013's downgrade batch-rebuilds `foods`, which recipe_ingredients
and shopping_list_items FK-reference under myMeal's enforced foreign keys
(extensions.py sets PRAGMA foreign_keys=ON). Its comment claimed "no foreign key
points at these columns, so no PRAGMA dance is needed" — but the hazard is a FK
pointing at the TABLE being rebuilt, not the column. On a populated SQLite DB
the rebuild's DROP raised "FOREIGN KEY constraint failed" and left an
_alembic_tmp corpse. An empty DB hid it.
"""
import os
import sqlite3
import subprocess


def _alembic(db, *args):
    env = dict(os.environ, MYMEAL_DATABASE_URL=f"sqlite:///{db}")
    return subprocess.run(["python3", "-m", "alembic", *args],
                          capture_output=True, text=True, env=env,
                          cwd=os.path.dirname(os.path.dirname(__file__)))


def _insert(conn, table, values):
    row = dict(values)
    for _cid, name, ctype, notnull, default, _pk in conn.execute(
            f"PRAGMA table_info({table})"):
        if name in row or not notnull or default is not None:
            continue
        if name in ("created_at", "updated_at"):
            row[name] = "2026-01-01 00:00:00"
        elif any(t in (ctype or "").upper() for t in ("INT", "REAL", "FLOA", "NUM")):
            row[name] = 0
        else:
            row[name] = ""
    cols = ",".join(row)
    marks = ",".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))


def _seed_head(tmp_path):
    db = str(tmp_path / "m13.db")
    r = _alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-800:]
    c = sqlite3.connect(db)
    _insert(c, "groups", {"id": "g", "name": "G"})
    _insert(c, "recipes", {"id": "r", "group_id": "g", "name": "Soup"})
    _insert(c, "foods", {"id": "f", "group_id": "g", "name": "Cinnamon"})
    # a recipe_ingredient referencing foods.id — the FK that breaks the rebuild
    _insert(c, "recipe_ingredients", {"id": "ri", "recipe_id": "r", "food_id": "f"})
    c.commit()
    c.close()
    return db


def test_0013_downgrade_survives_seeded_food_fk(tmp_path):
    db = _seed_head(tmp_path)
    r = _alembic(db, "downgrade", "0012_unit_conversions")
    assert r.returncode == 0, f"downgrade through 0013 failed:\n{r.stderr[-900:]}"
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM recipe_ingredients").fetchone()[0] == 1
    tmp = c.execute("SELECT name FROM sqlite_master WHERE name LIKE "
                    "'_alembic_tmp_%'").fetchall()
    c.close()
    assert tmp == [], f"leftover temp table wedges the next boot: {tmp}"
