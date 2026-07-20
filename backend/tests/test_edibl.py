"""Tests for the Edibl integration (both directions).

Edibl is myMeal's sibling food-inventory app. It already ships myMeal-named
endpoints; these tests cover myMeal's half: flattening the plan, the client
that pushes to and pulls from Edibl, and the endpoints that drive it. httpx is
stubbed at the client boundary — no live Edibl is contacted.
"""
from datetime import date

import httpx
import pytest

from app import create_app
from app.services.edibl import EdiblClient
from app.services.plan_ingredients import flatten_plan, upcoming_window


# --------------------------------------------------------------- fakes

class _Food:
    def __init__(self, name):
        self.name = name


class _Unit:
    def __init__(self, abbr, name=""):
        self.abbreviation = abbr
        self.name = name


class _Ing:
    def __init__(self, display="", quantity=0.0, unit=None, food=None):
        self.display, self.quantity, self.unit, self.food = display, quantity, unit, food


class _Recipe:
    def __init__(self, rid, servings=0, ingredients=()):
        self.id, self.servings, self.ingredients = rid, servings, list(ingredients)


class _Entry:
    def __init__(self, recipe, entry_date=None, meal_type="dinner", servings=0):
        self.recipe, self.date, self.meal_type, self.servings = \
            recipe, entry_date, meal_type, servings


# --------------------------------------------------------------- flattening

def test_flatten_prefers_food_name_over_display():
    r = _Recipe("r1", ingredients=[
        _Ing(display="2 cups flour", quantity=2, unit=_Unit("cup"), food=_Food("Flour")),
    ])
    items = flatten_plan([_Entry(r, date(2026, 7, 20))])
    assert items[0]["name"] == "Flour"
    assert items[0]["unit"] == "cup"
    assert items[0]["neededBy"] == "2026-07-20"
    assert items[0]["sourceRef"] == "mymeal:recipe:r1"


def test_flatten_falls_back_to_display_when_no_food():
    r = _Recipe("r2", ingredients=[_Ing(display="a pinch of salt", quantity=0)])
    items = flatten_plan([_Entry(r)])
    assert items[0]["name"] == "a pinch of salt"
    assert items[0]["unit"] == "count"


def test_flatten_scales_quantity_by_servings():
    r = _Recipe("r3", servings=2, ingredients=[
        _Ing(quantity=4, unit=_Unit("g"), food=_Food("Rice")),
    ])
    # Plan asks for 4 servings of a 2-serving recipe -> double.
    items = flatten_plan([_Entry(r, servings=4)])
    assert items[0]["quantity"] == 8


def test_flatten_does_not_scale_when_recipe_servings_unknown():
    """recipe.servings=0 must never cause a divide-by-zero or bogus scaling."""
    r = _Recipe("r4", servings=0, ingredients=[
        _Ing(quantity=3, unit=_Unit("g"), food=_Food("Sugar")),
    ])
    items = flatten_plan([_Entry(r, servings=10)])
    assert items[0]["quantity"] == 3


def test_flatten_skips_free_text_entries_without_a_recipe():
    """A meal with no recipe has no ingredient breakdown to reconcile."""
    assert flatten_plan([_Entry(recipe=None)]) == []


def test_flatten_skips_ingredients_with_no_usable_name():
    r = _Recipe("r5", ingredients=[_Ing(display="   ", quantity=1)])
    assert flatten_plan([_Entry(r)]) == []


def test_upcoming_window_is_bounded():
    start, end = upcoming_window(date(2026, 7, 20), 9999)
    assert (end - start).days == 365   # clamped, not 9999


# --------------------------------------------------------------- client

def _stub_transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture()
def patch_httpx(monkeypatch):
    """Route the client's httpx calls through a MockTransport handler."""
    def install(handler):
        transport = httpx.MockTransport(handler)

        def _get(url, params=None, headers=None, timeout=None):
            with httpx.Client(transport=transport) as c:
                return c.get(url, params=params, headers=headers)

        def _post(url, json=None, headers=None, timeout=None):
            with httpx.Client(transport=transport) as c:
                return c.post(url, json=json, headers=headers)

        monkeypatch.setattr(httpx, "get", _get)
        monkeypatch.setattr(httpx, "post", _post)
    return install


def test_unconfigured_client_reports_not_configured():
    c = EdiblClient(base_url="", token="")
    assert c.configured is False
    assert c.status() == {"configured": False, "reachable": False}
    assert c.get_stock()["items"] == []


def test_push_plan_sends_the_documented_body(patch_httpx):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        import json as _j
        captured["body"] = _j.loads(request.content)
        return httpx.Response(201, json={"upserted": 1})

    patch_httpx(handler)
    c = EdiblClient("http://edibl:8080", "tok-123")
    res = c.push_plan([{"name": "Flour", "quantity": 2, "unit": "cup"}], source="mymeal")

    assert res["reachable"] is True and res["data"]["upserted"] == 1
    assert captured["url"].endswith("/api/v1/integrations/mymeal/plan")
    assert captured["auth"] == "Bearer tok-123"
    assert captured["body"]["source"] == "mymeal"
    assert captured["body"]["items"][0]["name"] == "Flour"


def test_get_stock_normalises_to_pantry_shape(patch_httpx):
    def handler(request):
        return httpx.Response(200, json={"items": [
            {"name": "Milk", "quantity": 1, "unit": "L", "expiryDate": "2026-07-25"},
            {"product": {"name": "Eggs"}, "quantity": 6, "unit": "count"},
            {"name": "   ", "quantity": 9},   # unusable -> dropped
        ]})

    patch_httpx(handler)
    items = EdiblClient("http://edibl:8080", "t").get_stock()["items"]
    assert [i["name"] for i in items] == ["Milk", "Eggs"]
    assert items[0]["expiresAt"] == "2026-07-25"


def test_client_never_raises_when_edibl_is_down(patch_httpx):
    def handler(request):
        raise httpx.ConnectError("refused")

    patch_httpx(handler)
    c = EdiblClient("http://edibl:8080", "t")
    assert c.status()["reachable"] is False
    stock = c.get_stock()
    assert stock["configured"] is True
    assert stock["reachable"] is False
    assert stock["items"] == []
    assert stock["error"]  # a diagnostic message is present


def test_client_surfaces_http_errors_without_raising(patch_httpx):
    def handler(request):
        return httpx.Response(401, json={"error": "unauthorized"})

    patch_httpx(handler)
    res = EdiblClient("http://edibl:8080", "bad").push_plan([{"name": "x"}])
    assert res["reachable"] is True and res["status"] == 401


# --------------------------------------------------------------- errored Edibl (blocker regression)

def test_on_hand_unavailable_on_auth_error(patch_httpx):
    """BLOCKER regression: a 401 (bad token) must NOT read as available+empty.
    Before the fix, on_hand returned {available: True, items: []} and callers
    silently ranked against nothing while reporting Edibl healthy."""
    patch_httpx(lambda req: httpx.Response(401, json={"error": "unauthorized"}))
    inv = EdiblClient("http://edibl:8080", "bad-token").on_hand()
    assert inv["available"] is False
    assert inv["items"] == []
    assert "401" in inv["reason"] or "error" in inv["reason"].lower()


def test_on_hand_unavailable_on_server_error(patch_httpx):
    patch_httpx(lambda req: httpx.Response(500, json={"error": "boom"}))
    inv = EdiblClient("http://edibl:8080", "t").on_hand()
    assert inv["available"] is False and inv["items"] == []


def test_get_stock_reports_not_ok_on_http_error(patch_httpx):
    patch_httpx(lambda req: httpx.Response(403, json={}))
    stock = EdiblClient("http://edibl:8080", "t").get_stock()
    assert stock["ok"] is False
    assert stock["reachable"] is True          # server answered...
    assert stock["items"] == []                # ...but gave no usable stock


def test_edibl_stock_endpoint_502s_on_edibl_error(tmp_path, patch_httpx):
    patch_httpx(lambda req: httpx.Response(401, json={}))
    c = _client(tmp_path, DISABLE_AUTH=True,
                EDIBL_URL="http://edibl:8080", EDIBL_API_TOKEN="bad")
    r = c.get("/api/v1/edibl/stock")
    assert r.status_code == 502
    assert r.get_json()["items"] == []


def test_push_plan_endpoint_502s_on_edibl_error(tmp_path, monkeypatch):
    # A push with an errored Edibl must not report success. Supply a non-empty
    # plan so the endpoint reaches the push (it short-circuits on an empty one).
    import app.api.edibl as edibl_api
    from app.services.edibl import EdiblClient as _C
    monkeypatch.setattr(edibl_api, "flatten_plan", lambda entries: [{"name": "Flour"}])
    monkeypatch.setattr(_C, "push_plan",
                        lambda self, items, **kw: {"ok": False, "reachable": True,
                                                   "error": "HTTP 500"})
    c = _client(tmp_path, DISABLE_AUTH=True,
                EDIBL_URL="http://edibl:8080", EDIBL_API_TOKEN="t")
    r = c.post("/api/v1/edibl/push-plan", json={})
    assert r.status_code == 502


def test_suggest_reports_unavailable_when_edibl_errors(tmp_path):
    """/ai/suggest end to end: a configured-but-erroring Edibl reports the
    feature unavailable rather than an empty (misleading) ranking."""
    c = _client(tmp_path, DISABLE_AUTH=True,
                EDIBL_URL="http://edibl:8080", EDIBL_API_TOKEN="bad")
    # No live server -> the real client will fail to connect (unreachable),
    # which is also `ok: False`; assert the feature is reported unavailable.
    body = c.post("/api/v1/ai/suggest", json={}).get_json()
    assert body["ediblAvailable"] is False
    assert body["suggestions"] == []
    assert body["message"]


# --------------------------------------------------------------- endpoints

def _client(tmp_path, **settings):
    ns = {"DATA_DIR": str(tmp_path), "DATABASE_URL": f"sqlite:///{tmp_path/'e.db'}",
          "MCP_ENABLED": False}
    ns.update(settings)
    return create_app(type("C", (), ns)).test_client()


def test_plan_ingredients_endpoint_requires_auth(tmp_path):
    """Edibl authenticates with a token; the endpoint must reject anonymous."""
    assert _client(tmp_path).get("/api/v1/plan/ingredients").status_code == 401


def test_edibl_status_reports_disabled_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv("MYMEAL_DISABLE_AUTH", "true")
    c = _client(tmp_path, DISABLE_AUTH=True)
    body = c.get("/api/v1/edibl/status").get_json()
    assert body["configured"] is False


def test_push_plan_endpoint_400s_when_edibl_not_configured(tmp_path):
    c = _client(tmp_path, DISABLE_AUTH=True)
    r = c.post("/api/v1/edibl/push-plan", json={})
    assert r.status_code == 400
    assert "not configured" in r.get_json()["error"]


def test_edibl_settings_url_must_be_http(tmp_path):
    from app.settings import ConfigError, load_settings
    with pytest.raises(ConfigError) as exc:
        load_settings(env={"MYMEAL_EDIBL_URL": "edibl:8080"}, ha_options={})
    assert "http" in str(exc.value)


def test_edibl_token_without_url_warns(tmp_path):
    from app.settings import load_settings
    s = load_settings(env={"MYMEAL_EDIBL_API_TOKEN": "t"}, ha_options={})
    assert any("EDIBL_URL is not" in w for w in s.warnings)


def test_edibl_token_is_a_secret_and_redacted():
    from app.settings import load_settings
    s = load_settings(env={"MYMEAL_EDIBL_URL": "http://edibl:8080",
                           "MYMEAL_EDIBL_API_TOKEN": "sk-edibl-must-not-leak"},
                      ha_options={})
    import json as _j
    assert "sk-edibl-must-not-leak" not in _j.dumps(s.redacted())


# --------------------------------------------------------------- pantry removal

def test_pantry_endpoints_are_gone(tmp_path):
    """myMeal no longer owns a pantry; the old CRUD endpoints must not work.

    GET falls through to the 404 API handler; POST is 405 because only the SPA
    catch-all (GET-only) matches the path. Either way there is no pantry API.
    """
    c = _client(tmp_path, DISABLE_AUTH=True)
    assert c.get("/api/v1/pantry").status_code == 404
    post = c.post("/api/v1/pantry", json={"label": "rice"})
    assert post.status_code >= 400          # no create route
    assert post.status_code != 201          # definitely did not create anything


def test_no_pantry_table_in_a_fresh_database(tmp_path):
    """A new install must not create a pantry table at all."""
    from sqlalchemy import inspect
    from app.extensions import db

    app = create_app(type("C", (), {
        "DATA_DIR": str(tmp_path), "DATABASE_URL": f"sqlite:///{tmp_path/'fresh.db'}",
        "MCP_ENABLED": False}))
    with app.app_context():
        assert "pantry_items" not in inspect(db.engine).get_table_names()


def test_migration_drops_a_legacy_pantry_table(tmp_path):
    """An upgraded install that still has pantry_items has it dropped (the
    explicit, destructive choice) — and nothing else is touched."""
    import sqlite3
    from sqlalchemy import inspect
    from app.extensions import db

    dbfile = tmp_path / "legacy.db"
    con = sqlite3.connect(dbfile)
    con.execute("CREATE TABLE pantry_items (id TEXT PRIMARY KEY, label TEXT)")
    con.execute("INSERT INTO pantry_items VALUES ('1', 'old rice')")
    con.execute("CREATE TABLE keep_me (id TEXT)")   # unrelated table must survive
    con.commit()
    con.close()

    app = create_app(type("C", (), {
        "DATA_DIR": str(tmp_path), "DATABASE_URL": f"sqlite:///{dbfile}",
        "MCP_ENABLED": False}))
    with app.app_context():
        tables = inspect(db.engine).get_table_names()
    assert "pantry_items" not in tables      # dropped
    assert "keep_me" in tables                # scoped: everything else survives


# --------------------------------------------------------------- matcher

def test_rank_recipes_matches_by_name_only():
    """The matcher works off Edibl names (no myMeal food ids)."""
    from app.services.inventory import rank_recipes

    class Ing:
        def __init__(self, display):
            self.display, self.food = display, None

    class R:
        def __init__(self, rid, name, ings):
            self.id, self.name, self.slug = rid, name, name.lower()
            self.ingredients = [Ing(i) for i in ings]

    recipes = [R("a", "Salad", ["fresh lettuce", "ripe tomato"]),
               R("b", "Steak", ["beef", "salt"])]
    ranked = rank_recipes(recipes, [{"name": "lettuce"}, {"name": "tomato"}])
    assert ranked[0]["name"] == "Salad" and ranked[0]["coverage"] == 1.0
    assert ranked[1]["missingCount"] == 2


def test_rank_recipes_ignores_short_inventory_tokens():
    """A 2-char inventory name must not match everything by substring."""
    from app.services.inventory import rank_recipes

    class Ing:
        def __init__(self, d):
            self.display, self.food = d, None

    class R:
        def __init__(self):
            self.id, self.name, self.slug = "x", "X", "x"
            self.ingredients = [Ing("anchovy")]

    ranked = rank_recipes([R()], [{"name": "an"}])
    assert ranked[0]["haveCount"] == 0
