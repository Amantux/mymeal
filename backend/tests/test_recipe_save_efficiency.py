"""Saving a recipe must not re-load the group's foods per ingredient.

_find_or_create_food ran `query(Food).filter(group_id).all()` AND folded every
food's terms once PER ingredient row — O(rows × foods), a measured ~23s save on
a 4000-food group and a worker-exhaustion vector. The group's foods must be
loaded ONCE per save. Correctness hazard the fix must preserve: a food created
for an earlier row is visible to later rows (no duplicate for a repeated name).
"""
from sqlalchemy import event

from app.extensions import db
from app.models import Food


def _count_food_loads(app, fn):
    """Count SELECTs against the foods table issued while fn() runs."""
    seen = []

    def rec(conn, cursor, statement, params, context, executemany):
        low = statement.lower()
        # Only the full-catalog scans (group_id filter) — the O(rows × foods)
        # pathology. Per-PK loads (foods.id = ?) are counted separately.
        if "from foods" in low and "group_id" in low and "foods.id =" not in low:
            seen.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", rec)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", rec)
    return len(seen)


def test_food_loads_do_not_scale_with_catalog_or_ingredient_count(auth_client, app):
    """The pathology was a full group-food load PER ingredient row. With a big
    EXISTING catalog and ingredients that all match it (no new rows, so no
    post-insert default fetches), the whole save must issue a SMALL CONSTANT
    number of foods-table SELECTs — not one per row, and not scaling with the
    catalog."""
    names = [f"food{i:03d}" for i in range(40)]
    for n in names:
        auth_client.post("/api/v1/foods", json={"name": n})

    # 25 ingredients, each referencing an EXISTING food by name.
    ingredients = [{"display": f"1 {names[i]}", "food": names[i]} for i in range(25)]

    def save():
        r = auth_client.post("/api/v1/recipes",
                             json={"name": "Big", "ingredients": ingredients})
        assert r.status_code == 201

    loads = _count_food_loads(app, save)
    # One cache build + a small constant. Was ~25 (one full load per row).
    assert loads <= 4, f"{loads} foods-table SELECTs for 25 ingredients (O(rows) load)"


def test_a_repeated_new_name_in_one_save_makes_one_food(auth_client, app):
    """The cache-append correctness guard: two 'Vietnamese cinnamon' rows in the
    same save must resolve to ONE 'cinnamon' food, not two."""
    r = auth_client.post("/api/v1/recipes", json={
        "name": "Dup",
        "ingredients": [
            {"display": "1 tsp Vietnamese cinnamon", "food": "Vietnamese cinnamon"},
            {"display": "2 tsp Vietnamese cinnamon", "food": "Vietnamese cinnamon"},
        ],
    })
    assert r.status_code == 201
    with app.app_context():
        cinnamons = db.session.query(Food).filter_by(name="cinnamon").count()
        assert cinnamons == 1, f"{cinnamons} cinnamon rows created in one save"


def test_reading_a_recipe_does_not_lazy_load_each_ingredient_food(auth_client, app):
    """recipe_out serialises food/unit/refRecipe per ingredient; without eager
    loading that is one SELECT per ingredient (an N+1 on every GET /recipes/id
    and on the create/update response)."""
    from sqlalchemy import event

    foods = [f"ing{i:02d}" for i in range(15)]
    for n in foods:
        auth_client.post("/api/v1/foods", json={"name": n})
    created = auth_client.post("/api/v1/recipes", json={
        "name": "Wide",
        "ingredients": [{"display": f"1 {n}", "food": n} for n in foods],
    }).get_json()

    n = {"q": 0}

    def rec(conn, cursor, statement, params, context, executemany):
        low = statement.lower()
        if "from foods" in low or "from units" in low:
            n["q"] += 1

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", rec)
    try:
        r = auth_client.get(f"/api/v1/recipes/{created['id']}")
        assert r.status_code == 200 and len(r.get_json()["ingredients"]) == 15
    finally:
        event.remove(engine, "before_cursor_execute", rec)

    # Eager-loaded: a small constant (selectinload issues ~1 query per relation),
    # not ~15 (one food SELECT per ingredient).
    assert n["q"] <= 4, f"{n['q']} food/unit SELECTs to serialize 15 ingredients (N+1)"
