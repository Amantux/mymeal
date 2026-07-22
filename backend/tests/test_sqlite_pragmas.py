"""SQLite is tuned for concurrent workers (WAL + busy_timeout + FKs)."""
from sqlalchemy import text


def test_pragmas_applied(app):
    with app.app_context():
        from app.extensions import db
        if not db.engine.url.get_backend_name().startswith("sqlite"):
            return
        with db.engine.connect() as c:
            assert c.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
            assert c.execute(text("PRAGMA busy_timeout")).scalar() == 5000
            assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
