"""M3: meal planning, pantry, shopping lists, suggest, and AI plan."""


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


# --- Pantry --------------------------------------------------------------
def test_pantry_crud(auth_client):
    p = auth_client.post(
        "/api/v1/pantry", json={"label": "Rice", "quantity": 2, "unit": "kg"}
    ).get_json()
    assert p["label"] == "Rice"
    listing = auth_client.get("/api/v1/pantry").get_json()
    assert listing["items"][0]["id"] == p["id"]
    auth_client.put(f"/api/v1/pantry/{p['id']}", json={"quantity": 5})
    assert (
        auth_client.get("/api/v1/pantry").get_json()["items"][0]["quantity"] == 5
    )
    assert auth_client.delete(f"/api/v1/pantry/{p['id']}").status_code == 204


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


# --- Suggest (pantry-aware) ---------------------------------------------
def test_suggest_ranks_by_pantry_coverage(auth_client):
    _make_recipe(auth_client, "Salad", ["lettuce", "tomato"])
    _make_recipe(auth_client, "Steak", ["beef", "salt", "pepper"])
    auth_client.post("/api/v1/pantry", json={"label": "lettuce"})
    auth_client.post("/api/v1/pantry", json={"label": "tomato"})

    res = auth_client.post("/api/v1/ai/suggest", json={}).get_json()["suggestions"]
    # Salad is fully covered → ranked first with coverage 1.0
    assert res[0]["name"] == "Salad"
    assert res[0]["coverage"] == 1.0
    assert res[0]["missingCount"] == 0


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
    assert (
        auth_client.post("/api/v1/pantry", json={"label": "x", "quantity": "abc"}).status_code
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
