"""Shared harness for migration tests.

Lives here, not in a test module, because a test file importing helpers from
ANOTHER test file couples their collection order and hides shadowing (a merged
file ended up with two different `_insert`s, one silently overriding the other).
"""
import os
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
