"""Shared extension instances."""
import sqlite3

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """Tune SQLite for a multi-worker gunicorn deployment. Only touches SQLite.

    - WAL: readers don't block the writer (and vice-versa), so a page load no
      longer contends with a write. Persists on the DB file once set.
    - busy_timeout: on write contention, wait up to 5s for the lock instead of
      failing immediately with 'database is locked' — the concurrency bug this
      guards against under 2+ workers.
    - foreign_keys: enforce referential integrity (off by default in SQLite).
    - synchronous=NORMAL: safe with WAL and much faster than FULL.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()
