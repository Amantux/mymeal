"""M3: meal planning, shopping lists, suggest (Edibl-backed), and AI plan."""


def _make_recipe(client, name, ingredients):
    return client.post(
        "/api/v1/recipes",
        json={"name": name, "ingredients": [{"display": i} for i in ingredients]},
    ).get_json()


# --- Meal plans ----------------------------------------------------------
def test_mealplan_crud_and_range(auth_client):
    r = _make_recipe(auth_client, "Chili", ["beans", "beef"])
    e = auth_client.post(
        "/api/v1/mealplans",
        json={"date": "2026-07-20", "mealType": "dinner", "recipeId": r["id"]},
    ).get_json()
    assert e["recipe"]["name"] == "Chili"

    # In range
    inrange = auth_client.get(
        "/api/v1/mealplans?start=2026-07-19&end=2026-07-21"
    ).get_json()
    assert inrange["items"][0]["id"] == e["id"]
    # Out of range
    outrange = auth_client.get(
        "/api/v1/mealplans?start=2026-08-01&end=2026-08-07"
    ).get_json()
    assert outrange["items"] == []

    assert auth_client.delete(f"/api/v1/mealplans/{e['id']}").status_code == 204




# --- Shopping lists + consolidation --------------------------------------
def test_shopping_list_from_recipes_consolidates(auth_client):
    _make_recipe(auth_client, "Toast", ["2 eggs", "bread"])
    _make_recipe(auth_client, "Omelette", ["2 eggs", "cheese"])
    recipes = auth_client.get("/api/v1/recipes").get_json()["items"]
    ids = [r["id"] for r in recipes]

    sl = auth_client.post(
        "/api/v1/shopping-lists", json={"name": "Weekly"}
    ).get_json()
    res = auth_client.post(
        f"/api/v1/shopping-lists/{sl['id']}/from-recipes",
        json={"recipeIds": ids},
    ).get_json()
    displays = sorted(i["display"] for i in res["items"])
    # "2 eggs" appears in both recipes but consolidates to a single line.
    assert displays.count("2 eggs") == 1
    assert "bread" in displays and "cheese" in displays


def test_shopping_item_check_and_delete(auth_client):
    sl = auth_client.post("/api/v1/shopping-lists", json={}).get_json()
    item = auth_client.post(
        f"/api/v1/shopping-lists/{sl['id']}/items", json={"display": "milk"}
    ).get_json()
    checked = auth_client.put(
        f"/api/v1/shopping-lists/items/{item['id']}", json={"checked": True}
    ).get_json()
    assert checked["checked"] is True
    assert (
        auth_client.delete(f"/api/v1/shopping-lists/items/{item['id']}").status_code
        == 204
    )


# --- Suggest (inventory-aware, backed by Edibl) -------------------------
def test_suggest_ranks_by_edibl_inventory_coverage(auth_client, monkeypatch):
    """Inventory now comes from Edibl, not a local pantry. Stub the on-hand
    inventory and confirm ranking still works end to end."""
    from app.services.edibl import EdiblClient

    _make_recipe(auth_client, "Salad", ["lettuce", "tomato"])
    _make_recipe(auth_client, "Steak", ["beef", "salt", "pepper"])
    monkeypatch.setattr(EdiblClient, "on_hand", lambda self: {
        "available": True, "items": [{"name": "lettuce"}, {"name": "tomato"}]})

    body = auth_client.post("/api/v1/ai/suggest", json={}).get_json()
    assert body["ediblAvailable"] is True
    res = body["suggestions"]
    assert res[0]["name"] == "Salad"       # fully covered -> first
    assert res[0]["coverage"] == 1.0 and res[0]["missingCount"] == 0


def test_suggest_reports_unavailable_without_edibl(auth_client):
    """Standalone, no Edibl: the feature is cleanly unavailable, not a 500 and
    not a meaningless ranking against nothing."""
    _make_recipe(auth_client, "Salad", ["lettuce"])
    body = auth_client.post("/api/v1/ai/suggest", json={}).get_json()
    assert body["ediblAvailable"] is False
    assert body["suggestions"] == []
    assert "Edibl" in body["message"]


# --- AI plan (fake provider) --------------------------------------------
def test_ai_plan_creates_entries(auth_client, monkeypatch):
    import app.api.ai as ai_api
    from app.services.ai.base import AIProvider, ChatResult

    r = _make_recipe(auth_client, "Pasta Night", ["pasta"])
    rid = r["id"]

    class PlanProvider(AIProvider):
        name = "fake"

        def available(self):
            return True

        def _complete(self, system, prompt, max_tokens):
            return (
                '{"days":[{"offset":0,"meals":[{"mealType":"dinner",'
                f'"recipeId":"{rid}","title":""}}]}},'
                '{"offset":1,"meals":[{"mealType":"dinner","recipeId":"",'
                '"title":"Leftovers"}]}]}'
            )

        def chat(self, messages, system="", tools=None, max_tokens=2048):
            return ChatResult(content="ok")

    monkeypatch.setattr(ai_api, "get_provider", lambda: PlanProvider())
    res = auth_client.post(
        "/api/v1/ai/plan", json={"start": "2026-07-20", "days": 2}
    )
    assert res.status_code == 201
    entries = res.get_json()["entries"]
    assert len(entries) == 2
    assert entries[0]["recipeId"] == rid
    assert entries[1]["title"] == "Leftovers"
    # Bogus recipe ids would be dropped to null; valid one is kept.
    assert entries[1]["recipeId"] is None


def _fake_plan_provider(raw_json):
    from app.services.ai.base import AIProvider, ChatResult

    class P(AIProvider):
        name = "fake"

        def available(self):
            return True

        def _complete(self, system, prompt, max_tokens):
            return raw_json

        def chat(self, messages, system="", tools=None, max_tokens=2048):
            return ChatResult(content="ok")

    return P()


def test_ai_plan_survives_offshape_provider_json(auth_client, monkeypatch):
    """Off-shape provider output must degrade to a clean response, not 500."""
    import app.api.ai as ai_api

    for raw in (
        '{"days":"nonsense"}',
        '{"days":[{"offset":0,"meals":["dinner"]}]}',
        '{"days":[42,{"offset":"x","meals":null}]}',
    ):
        monkeypatch.setattr(ai_api, "get_provider", lambda raw=raw: _fake_plan_provider(raw))
        r = auth_client.post("/api/v1/ai/plan", json={"days": 1})
        assert r.status_code < 500


def test_ai_plan_survives_malformed_request_body(auth_client, monkeypatch):
    import app.api.ai as ai_api

    monkeypatch.setattr(
        ai_api, "get_provider", lambda: _fake_plan_provider('{"days":[]}')
    )
    r = auth_client.post(
        "/api/v1/ai/plan",
        json={"mealTypes": [1, 2], "preferences": 5, "servings": "lots"},
    )
    assert r.status_code < 500


def test_write_endpoints_coerce_bad_numbers(auth_client):
    """Non-numeric servings/quantity must not 500 on the M3 write endpoints."""
    assert (
        auth_client.post("/api/v1/mealplans", json={"servings": "lots"}).status_code
        == 201
    )
    sl = auth_client.post("/api/v1/shopping-lists", json={}).get_json()
    assert (
        auth_client.post(
            f"/api/v1/shopping-lists/{sl['id']}/items",
            json={"display": "y", "quantity": [1, 2]},
        ).status_code
        == 201
    )


def test_ai_plan_without_provider_503(auth_client, monkeypatch):
    import app.api.ai as ai_api
    from app.services.ai.base import ProviderError

    def _none():
        raise ProviderError("no provider")

    monkeypatch.setattr(ai_api, "get_provider", _none)
    assert auth_client.post("/api/v1/ai/plan", json={}).status_code == 503


def test_assistant_shopping_action_id_is_a_real_deletable_item(auth_client, monkeypatch):
    """The undo id in a chat action must actually address a real shopping item,
    so the frontend's DELETE reverses exactly what was added."""
    import app.api.chat as chat_api

    class FakeProvider:
        def __init__(self):
            self.calls = 0
        def chat(self, messages, system=None, tools=None):
            self.calls += 1
            class R:
                pass
            r = R()
            if self.calls == 1:
                class Call:
                    name = "add_to_shopping_list"
                    arguments = {"item": "eggs"}
                r.content = ""
                r.tool_calls = [Call()]
            else:
                r.content = "Added eggs."
                r.tool_calls = []
            return r

    monkeypatch.setattr(chat_api, "get_provider", lambda: FakeProvider())
    body = auth_client.post("/api/v1/ai/chat", json={"message": "add eggs"}).get_json()
    actions = body["actions"]
    assert actions and actions[0]["undo"]["kind"] == "shopping_item"
    item_id = actions[0]["undo"]["id"]
    # The undo call the frontend would make must succeed, then be idempotent-ish.
    assert auth_client.delete(f"/api/v1/shopping-lists/items/{item_id}").status_code == 204
    assert auth_client.delete(f"/api/v1/shopping-lists/items/{item_id}").status_code == 404
