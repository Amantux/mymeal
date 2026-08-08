"""High-backlog correctness fixes (sanity-check pass)."""
from app.services import units


# --- 1. "1½ cups" must parse to qty 1.5 with the unit kept -------------------
def test_unicode_fraction_after_whole_number_parses():
    for text, qty in [("1½ cups sugar", 1.5), ("1 ½ cups sugar", 1.5),
                      ("2¼ tsp salt", 2.25), ("½ cup milk", 0.5)]:
        p = units.parse_line(text)
        assert p["qty"] == qty, f"{text!r} -> qty {p['qty']}"
        assert p["unit"] is not None, f"{text!r} lost its unit"


# --- 2. /search ranks BEFORE limiting ---------------------------------------
def test_search_returns_the_best_match_even_when_it_sorts_late(auth_client):
    # 30 filler recipes sorting before "zz…", plus an exact-name target.
    # 30 substring matches that SORT BEFORE the exact-name target, so a
    # pre-rank alphabetical LIMIT loads only fillers and never sees the exact
    # match (which the ranker would put first).
    for i in range(30):
        auth_client.post("/api/v1/recipes", json={"name": f"aaa soup {i:02d}"})
    auth_client.post("/api/v1/recipes", json={"name": "soup"})   # exact, sorts last
    body = auth_client.get("/api/v1/search?q=soup&types=recipe&limit=5").get_json()
    names = [r["name"] for r in body["results"]]
    assert names and names[0] == "soup", \
        f"exact-name match not ranked first / cut by pre-rank limit: {names}"


# --- 4. version restore / clearing cookTemperatureC -------------------------
def test_clearing_cook_temperature_via_put(auth_client):
    r = auth_client.post("/api/v1/recipes",
                         json={"name": "Bake", "cookTemperatureC": 180}).get_json()
    assert r["cookTemperatureC"] == 180
    auth_client.put(f"/api/v1/recipes/{r['id']}", json={"cookTemperatureC": None})
    after = auth_client.get(f"/api/v1/recipes/{r['id']}").get_json()
    assert after["cookTemperatureC"] is None, "null cookTemperatureC did not clear"


def test_version_restore_clears_cook_temperature(auth_client):
    r = auth_client.post("/api/v1/recipes", json={"name": "R"}).get_json()  # no temp
    v = auth_client.post(f"/api/v1/recipes/{r['id']}/versions",
                         json={"label": "snap"}).get_json()
    auth_client.put(f"/api/v1/recipes/{r['id']}", json={"cookTemperatureC": 200})
    auth_client.post(f"/api/v1/recipes/{r['id']}/versions/{v['id']}/restore")
    after = auth_client.get(f"/api/v1/recipes/{r['id']}").get_json()
    assert after["cookTemperatureC"] is None, "restore kept the current temp"


# --- 3. shopping from-mealplan scales by servings and sums duplicates -------
def test_from_mealplan_scales_and_sums_duplicate_entries(auth_client):
    r = auth_client.post("/api/v1/recipes", json={
        "name": "Chili", "servings": 4,
        "ingredients": [{"display": "2 cups beans", "food": "beans",
                         "quantity": 2, "unit": "cup"}],
    }).get_json()
    # plan it TWICE, once at double servings
    auth_client.post("/api/v1/mealplans",
                     json={"date": "2026-03-02", "recipeId": r["id"],
                           "meal": "dinner", "servings": 8})
    auth_client.post("/api/v1/mealplans",
                     json={"date": "2026-03-05", "recipeId": r["id"],
                           "meal": "dinner", "servings": 4})
    sl = auth_client.post("/api/v1/shopping-lists", json={"name": "L"}).get_json()
    res = auth_client.post(f"/api/v1/shopping-lists/{sl['id']}/from-mealplan",
                           json={"start": "2026-03-01", "end": "2026-03-31"}).get_json()
    beans = [i for i in res["items"] if i["display"] == "beans"]
    assert beans, f"no beans line: {[i['display'] for i in res['items']]}"
    # 8 servings (×2 of 4) → 4 cups, + 4 servings (×1) → 2 cups = 6 cups
    assert beans[0]["quantity"] == 6, f"expected 6 cups, got {beans[0]['quantity']}"


# --- 5. list endpoints don't lazy-load per row ------------------------------
def test_mealplan_list_eager_loads_recipes(auth_client, app):
    from sqlalchemy import event
    from app.extensions import db
    for i in range(10):
        rr = auth_client.post("/api/v1/recipes", json={"name": f"m{i}"}).get_json()
        auth_client.post("/api/v1/mealplans",
                         json={"date": f"2026-04-{i+1:02d}", "recipeId": rr["id"],
                               "meal": "dinner"})
    n = {"q": 0}

    def rec(conn, cur, st, p, ctx, em):
        low = st.lower()
        if low.startswith("select") and (" from recipe_videos" in low
                                          or " from recipes where" in low):
            n["q"] += 1
    with app.app_context():
        eng = db.engine
    event.listen(eng, "before_cursor_execute", rec)
    try:
        auth_client.get("/api/v1/mealplans?start=2026-04-01&end=2026-04-30")
    finally:
        event.remove(eng, "before_cursor_execute", rec)
    assert n["q"] <= 4, f"{n['q']} per-recipe SELECTs for 10 mealplan entries (N+1)"


def test_recipe_list_eager_loads_videos(auth_client, app):
    from sqlalchemy import event
    from app.extensions import db
    for i in range(12):
        auth_client.post("/api/v1/recipes", json={"name": f"v{i}"})
    n = {"q": 0}

    def rec(conn, cur, st, p, ctx, em):
        if st.lower().startswith("select") and " from recipe_videos" in st.lower():
            n["q"] += 1
    with app.app_context():
        eng = db.engine
    event.listen(eng, "before_cursor_execute", rec)
    try:
        auth_client.get("/api/v1/recipes")
    finally:
        event.remove(eng, "before_cursor_execute", rec)
    assert n["q"] <= 2, f"{n['q']} recipe_videos SELECTs listing 12 recipes (N+1)"
