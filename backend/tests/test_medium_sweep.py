"""Medium-severity sweep fixes.

Each of these is user-reachable wrong behaviour found by a review pass:
negative limits, upstream bodies echoed to callers, unclamped strings that 500
on Postgres, and a shopping-list build that silently buys the whole plan history.
"""
from datetime import date, timedelta


def _recipe(c, name="Chicken Soup", **kw):
    return c.post("/api/v1/recipes", json={"name": name, **kw}).get_json()


# ---- 1. negative limit ------------------------------------------------------

def test_search_negative_limit_is_clamped_not_a_negative_slice(auth_client):
    """limit=-1 used to slice ranked[:-1] — dropping the LAST (and on a
    single-match query, the only) result — and is a hard error on Postgres
    (LIMIT must not be negative). Clamped to 1, it returns the TOP match."""
    _recipe(auth_client, "Chicken Soup")
    _recipe(auth_client, "Chicken Pie")

    neg = auth_client.get("/api/v1/search?q=chicken&limit=-1")
    one = auth_client.get("/api/v1/search?q=chicken&limit=1")

    assert neg.status_code == 200
    neg_names = [x["name"] for x in neg.get_json()["results"]]
    one_names = [x["name"] for x in one.get_json()["results"]]
    assert neg_names == one_names, "negative limit did not clamp to the top match"
    assert len(neg_names) == 1


def test_search_negative_limit_still_finds_a_lone_match(auth_client):
    """The sharpest form: with ONE match, ranked[:-1] returned nothing at all."""
    _recipe(auth_client, "Unique Pie")

    r = auth_client.get("/api/v1/search?q=unique&limit=-1")

    assert r.status_code == 200
    assert r.get_json()["total"] == 1, "a negative limit hid the only match"


# ---- 4. from-mealplan window ------------------------------------------------

def test_from_mealplan_without_a_range_does_not_buy_the_whole_history(auth_client):
    """An omitted/unparseable range meant NO date filter, so a list built from
    'the plan' silently included years of past meals."""
    # WITH ingredients: without them `added` is 0 whatever the window does, and
    # the test would pass against the bug.
    rec = _recipe(auth_client, "Old Meal", ingredients=[{"display": "1 onion"}])
    auth_client.post("/api/v1/mealplans",
                     json={"date": "2020-01-01", "recipeId": rec["id"]})
    sl = auth_client.post("/api/v1/shopping-lists", json={"name": "L"}).get_json()

    r = auth_client.post(f"/api/v1/shopping-lists/{sl['id']}/from-mealplan", json={})

    assert r.status_code in (201, 200)
    assert r.get_json()["added"] == 0, "bought a meal planned in 2020"


def test_from_mealplan_default_window_includes_upcoming(auth_client):
    rec = _recipe(auth_client, "Soon Meal",
                  ingredients=[{"display": "1 onion"}])
    soon = (date.today() + timedelta(days=2)).isoformat()
    auth_client.post("/api/v1/mealplans", json={"date": soon, "recipeId": rec["id"]})
    sl = auth_client.post("/api/v1/shopping-lists", json={"name": "L"}).get_json()

    r = auth_client.post(f"/api/v1/shopping-lists/{sl['id']}/from-mealplan", json={})

    assert r.get_json()["added"] >= 1, "default window missed an upcoming meal"


# ---- 5. unclamped strings ---------------------------------------------------

def test_long_ingredient_note_and_section_are_clamped(auth_client):
    """display/unit/food names are clamped with a comment saying an unclamped
    value 500s on Postgres — note and section were missed."""
    r = auth_client.post("/api/v1/recipes", json={
        "name": "Big", "ingredients": [
            {"display": "1 onion", "note": "n" * 2000, "section": "s" * 900}]})

    assert r.status_code in (200, 201)
    ing = r.get_json()["ingredients"][0]
    assert len(ing.get("note") or "") <= 512
    assert len(ing.get("section") or "") <= 255


# ---- 11. invalid recipeId ---------------------------------------------------

def test_unknown_recipe_id_on_a_meal_plan_entry_is_refused(auth_client):
    """Silently dropping it returned 201 with a blank 'Meal' slot."""
    r = auth_client.post("/api/v1/mealplans",
                         json={"date": "2026-01-01", "recipeId": "no-such-recipe"})
    assert r.status_code == 422, f"got {r.status_code}"


# ---- 2 / 9. upstream responses never crash or leak --------------------------

def test_edibl_client_survives_a_non_json_200(monkeypatch):
    """Pointing the Edibl URL at any host serving HTML (ingress root, wrong
    port) raised ValueError out of r.json() — a 500 on /edibl/status, from a
    client whose whole contract is to report unreachable instead."""
    from app.services import edibl as edibl_mod

    class _Resp:
        status_code = 200
        request = type("R", (), {"url": "http://x/api/v1/health"})()

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("not json")

    client = edibl_mod.EdiblClient(base_url="http://x", token="t")
    out = client._finish(lambda: _Resp())

    assert out["ok"] is False
    assert out["reachable"] is False
    assert "json" in out["error"].lower()


def test_mcp_plan_week_does_not_echo_the_upstream_body(monkeypatch):
    """A raw upstream body can carry a DSN or key material."""
    import httpx

    import mcp_server

    secret = "postgresql://user:SUPERSECRET@db:5432/x"

    class _Resp:
        status_code = 500
        text = secret

        def json(self):
            return {"error": "planning is unavailable"}

    def _boom(*a, **k):
        raise httpx.HTTPStatusError("x", request=None, response=_Resp())

    monkeypatch.setattr(mcp_server, "_post", _boom)
    out = mcp_server.plan_week.fn() if hasattr(mcp_server.plan_week, "fn") \
        else mcp_server.plan_week()

    assert "SUPERSECRET" not in str(out), "leaked the upstream body"
    assert "planning is unavailable" in out["error"]


# ---- 3. ?servings=N must scale the structured quantity, not just display -----

def test_servings_scaling_also_scales_the_structured_quantity(auth_client):
    """display said '4 cup flour' while quantity stayed 2.0 — any consumer
    reading quantity (shopping, MCP, HA, the SPA) got the unscaled number."""
    rec = auth_client.post("/api/v1/recipes", json={
        "name": "Scale Me", "servings": 2,
        "ingredients": [{"display": "2 cups flour", "quantity": 2}]}).get_json()

    got = auth_client.get(f"/api/v1/recipes/{rec['id']}?servings=4").get_json()

    ing = got["ingredients"][0]
    assert got["scaledServings"] == 4
    assert ing["quantity"] == 4, f"display scaled but quantity did not: {ing}"


# ---- 6. N+1 on the meal plan (HA polls the summary on a timer) --------------

def test_mealplans_list_does_not_n_plus_one(auth_client, app):
    from app.extensions import db
    rec_a = auth_client.post("/api/v1/recipes", json={"name": "A"}).get_json()
    rec_b = auth_client.post("/api/v1/recipes", json={"name": "B"}).get_json()
    for i in range(8):
        auth_client.post("/api/v1/mealplans", json={
            "date": f"2026-03-0{(i % 8) + 1}",
            "recipeId": (rec_a if i % 2 else rec_b)["id"]})

    seen = []
    from sqlalchemy import event

    def _count(conn, cursor, statement, *a):
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    # cold session: a warm identity map hides the N+1 entirely
    with app.app_context():
        db.session.remove()
        engine = db.engine
    event.listen(engine, "before_cursor_execute", _count)
    try:
        r = auth_client.get("/api/v1/mealplans")
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert r.status_code == 200
    assert len(seen) <= 8, f"{len(seen)} SELECTs for 8 entries / 2 recipes"
