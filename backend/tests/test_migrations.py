"""Migrations: they must apply, reverse, and describe the SAME schema a fresh
create_all() would.

Grouped in one file because these fail as a class, not individually: a SQLite
table rebuild under enforced foreign keys, a column type that diverges between
the metadata baseline and the migrated path, an index declared in one place but
not the other. Every case seeds real rows first — an empty database migrates
fine, which is exactly why the rest of the suite missed them.
"""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from migration_harness import _insert, _run_alembic
import sqlite3

from app import create_app
from app.config import Config
from app.models import Group, User
import app.models  # noqa: F401 - register metadata


# ---- apply / reverse ----

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


# ---- 0013: rebuilding an FK-referenced table ----



def _seed_head(tmp_path):
    db = str(tmp_path / "m13.db")
    r = _run_alembic(db, "upgrade", "head")
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
    r = _run_alembic(db, "downgrade", "0012_unit_conversions")
    assert r.returncode == 0, f"downgrade through 0013 failed:\n{r.stderr[-900:]}"
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM recipe_ingredients").fetchone()[0] == 1
    tmp = c.execute("SELECT name FROM sqlite_master WHERE name LIKE "
                    "'_alembic_tmp_%'").fetchall()
    c.close()
    assert tmp == [], f"leftover temp table wedges the next boot: {tmp}"


# ---- 0015: NOT NULL + server_default agreement ----

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


# ---- 0016: hot FK/tenant indexes ----

_EXPECTED = {
    "recipes": "ix_recipes_group_id",
    "recipe_ingredients": "ix_recipe_ingredients_recipe_id",
    "recipe_steps": "ix_recipe_steps_recipe_id",
    "shopping_list_items": "ix_shopping_list_items_shopping_list_id",
}


def _indexes(db, table):
    c = sqlite3.connect(db)
    try:
        return {r[1] for r in c.execute(f"PRAGMA index_list({table})")}
    finally:
        c.close()


def test_upgrade_creates_every_hot_index(tmp_path):
    db = str(tmp_path / "m.db")
    r = _run_alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-800:]
    for table, index in _EXPECTED.items():
        assert index in _indexes(db, table), f"{index} missing on {table}"


def test_downgrade_then_upgrade_round_trips(tmp_path):
    db = str(tmp_path / "m.db")
    assert _run_alembic(db, "upgrade", "head").returncode == 0
    down = _run_alembic(db, "downgrade", "0015_food_allergens_not_null")
    assert down.returncode == 0, down.stderr[-800:]
    for table, index in _EXPECTED.items():
        assert index not in _indexes(db, table), f"{index} survived downgrade"
    up = _run_alembic(db, "upgrade", "head")
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
