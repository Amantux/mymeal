"""Schema-init / Alembic migration behaviour.

Guards the data-loss-adjacent paths that run at every startup: fresh build,
adoption of an existing pre-Alembic DB (data preserved, legacy pantry dropped),
healing of an interrupted first boot, and the URL-encoded-password crash.
"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register metadata
from app import create_app
from app.config import Config
from app.models import Group, User


def _cfg(tmp_path, name="t.db"):
    class C(Config):
        DATA_DIR = str(tmp_path)
        DATABASE_URL = f"sqlite:///{tmp_path}/{name}"
        SECRET_KEY = "test-secret-key-that-is-long-enough-32b"
        DISABLE_AUTH = True

    return C


def test_fresh_db_builds_full_schema(tmp_path):
    app = create_app(_cfg(tmp_path))
    with app.app_context():
        insp = inspect(app.extensions["sqlalchemy"].engine)
        assert insp.has_table("recipes")
        assert insp.has_table("alembic_version")


def test_adopt_existing_db_preserves_data_and_drops_pantry(tmp_path):
    # A pre-Alembic install: full schema + a legacy pantry_items + a data row.
    url = f"sqlite:///{tmp_path}/pre.db"
    from app.extensions import db

    eng = create_engine(url)
    db.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE pantry_items (id VARCHAR PRIMARY KEY)"))
    session = Session(bind=eng)
    session.add(Group(name="Keepme"))
    session.commit()
    session.close()
    eng.dispose()

    app = create_app(_cfg(tmp_path, "pre.db"))
    with app.app_context():
        insp = inspect(db.engine)
        assert not insp.has_table("pantry_items")  # 0002 ran
        assert db.session.query(Group).filter_by(name="Keepme").count() == 1  # kept


def test_partial_first_boot_is_healed(tmp_path):
    # Interrupted first boot: only groups + users exist, no alembic_version.
    # Must fill the gaps, not stamp-and-skip (which would leave recipes missing).
    url = f"sqlite:///{tmp_path}/partial.db"
    eng = create_engine(url)
    Group.__table__.create(eng)
    User.__table__.create(eng)
    eng.dispose()

    app = create_app(_cfg(tmp_path, "partial.db"))
    with app.app_context():
        from app.extensions import db

        assert inspect(db.engine).has_table("recipes")


def test_percent_in_db_url_does_not_crash_alembic(tmp_path):
    # Regression for the ConfigParser %-interpolation crash: a '%' in the URL
    # (routine once a password is URL-encoded, e.g. %40 for '@') must not blow up.
    app = create_app(_cfg(tmp_path, "te%40st.db"))
    with app.app_context():
        from app.extensions import db

        assert inspect(db.engine).has_table("recipes")
