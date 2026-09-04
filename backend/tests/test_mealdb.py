"""TheMealDB import-by-name source — the one that works with nothing configured.

Before it, "By name" was a dead end on a fresh install: the branch demanded an
Ollama web-search key and 503'd without one. TheMealDB returns fully structured
recipes, so a hit never touches the AI provider at all.
"""
import httpx

import app.api.ai as ai_api
from app.services import mealdb

MEAL = {
    "idMeal": "52772",
    "strMeal": "Teriyaki Chicken Casserole",
    "strCategory": "Chicken",
    "strArea": "Japanese",
    "strTags": "Meat,Casserole",
    "strInstructions": "Preheat oven to 350F.\r\nSTEP 2: Combine the soy sauce "
                       "and water.\r\n\r\n3) Bake for 45 minutes.",
    "strMealThumb": "https://www.themealdb.com/images/media/meals/wvpsxx.jpg",
    "strSource": "https://example.com/teriyaki",
    "strIngredient1": "soy sauce", "strMeasure1": "3/4 cup",
    "strIngredient2": "water", "strMeasure2": "1/2 cup",
    "strIngredient3": "brown sugar", "strMeasure3": "1/4 cup",
    "strIngredient4": "", "strMeasure4": " ",       # the API's blank padding
    "strIngredient5": None, "strMeasure5": None,
}


def _mock(monkeypatch, handler):
    """Route mealdb's real httpx.get through a MockTransport, keeping the real
    request-building code in play rather than stubbing the service function."""
    def fake_get(url, params=None, timeout=None):
        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as c:
            return c.get(url, params=params)
    monkeypatch.setattr(mealdb.httpx, "get", fake_get)


def _serve(meals_by_query):
    calls = []

    def handler(request):
        q = request.url.params.get("s", "")
        calls.append(str(request.url))
        return httpx.Response(200, json={"meals": meals_by_query.get(q)})
    return handler, calls


def test_a_hit_becomes_a_complete_import_payload(monkeypatch):
    handler, _ = _serve({"teriyaki chicken": [MEAL]})
    _mock(monkeypatch, handler)

    got = mealdb.search("teriyaki chicken")

    assert got["name"] == "Teriyaki Chicken Casserole"
    # The twenty numbered column pairs become normal display lines, blanks dropped.
    assert [i["display"] for i in got["ingredients"]] == [
        "3/4 cup soy sauce", "1/2 cup water", "1/4 cup brown sugar"]
    # The instruction blob splits into steps with the numbering prefixes removed.
    assert [s["text"] for s in got["steps"]] == [
        "Preheat oven to 350F.",
        "Combine the soy sauce and water.",
        "Bake for 45 minutes.",
    ]
    assert got["imageUrl"].endswith(".jpg")
    assert got["sourceUrl"] == "https://example.com/teriyaki"
    assert set(got["tags"]) == {"Meat", "Casserole", "Chicken", "Japanese"}
    assert got["servings"] == 0        # TheMealDB doesn't state one; not invented


def test_noise_words_are_retried_without(monkeypatch):
    """People type "beef wellington recipe"; the database titles don't say
    "recipe". A miss retries with the noise words dropped."""
    handler, calls = _serve({"beef wellington recipe": None,
                             "beef wellington": [dict(MEAL, strMeal="Beef Wellington")]})
    _mock(monkeypatch, handler)

    got = mealdb.search("beef wellington recipe")

    assert got["name"] == "Beef Wellington"
    assert len(calls) == 2


def test_a_miss_is_none_never_an_exception(monkeypatch):
    _mock(monkeypatch, lambda request: httpx.Response(200, json={"meals": None}))
    assert mealdb.search("no such dish anywhere") is None

    _mock(monkeypatch, lambda request: httpx.Response(500, text="upstream broke"))
    assert mealdb.search("anything") is None      # fail open: fall through

    _mock(monkeypatch, lambda request: httpx.Response(200, text="not json"))
    assert mealdb.search("anything") is None


def test_a_meal_with_no_ingredients_is_not_a_result(monkeypatch):
    empty = {k: ("" if k.startswith("strIngredient") else v) for k, v in MEAL.items()}
    _mock(monkeypatch, lambda request: httpx.Response(200, json={"meals": [empty]}))

    assert mealdb.search("broken row") is None


def test_missing_source_url_falls_back_to_the_mealdb_page(monkeypatch):
    _mock(monkeypatch, lambda request: httpx.Response(
        200, json={"meals": [dict(MEAL, strSource="")]}))

    got = mealdb.search("teriyaki")

    assert got["sourceUrl"] == "https://www.themealdb.com/meal/52772"


# --- Through the endpoint ----------------------------------------------------

def test_by_name_import_works_with_no_keys_at_all(auth_client, monkeypatch):
    """The point of the feature: a fresh install, no Ollama search key, no AI
    provider — and "By name" still imports."""
    def no_provider():
        from app.services.ai.base import ProviderError
        raise ProviderError("none configured")
    monkeypatch.setattr(ai_api, "get_provider", no_provider)
    handler, _ = _serve({"teriyaki chicken": [MEAL]})
    _mock(monkeypatch, handler)
    # Image download would hit the network; the URL guard rejects nothing here,
    # so stub the best-effort downloader instead.
    monkeypatch.setattr(ai_api, "download_image_to_recipe", lambda *a, **k: None)

    r = auth_client.post("/api/v1/ai/import", json={"query": "teriyaki chicken"})

    assert r.status_code == 201
    body = r.get_json()
    assert body["name"] == "Teriyaki Chicken Casserole"
    assert len(body["ingredients"]) == 3
    assert len(body["steps"]) == 3


def test_a_mealdb_miss_without_a_search_key_is_a_404_not_a_503(auth_client,
                                                               monkeypatch):
    """A miss now means "not found", not "you must configure a key" — the key
    only gates the WIDER search, and the message says so."""
    def no_provider():
        from app.services.ai.base import ProviderError
        raise ProviderError("none configured")
    monkeypatch.setattr(ai_api, "get_provider", no_provider)
    _mock(monkeypatch, lambda request: httpx.Response(200, json={"meals": None}))

    r = auth_client.post("/api/v1/ai/import", json={"query": "extremely obscure dish"})

    assert r.status_code == 404
    assert "web search isn't configured" in r.get_json()["error"]


def test_the_key_setting_is_used_and_defaults_to_the_public_one(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"meals": None})
    _mock(monkeypatch, handler)

    mealdb.search("x")
    assert seen and "/api/json/v1/1/" in seen[0]   # the public test key


def test_payload_shape_matches_the_importers(monkeypatch):
    """Same guard as the paste parser: two builders that drift is how a field
    goes silently missing on one path. sourceUrl is extra by design — the other
    builders add it at the call site."""
    from app.services.ai.recipe_import import normalize_jsonld

    handler, _ = _serve({"teriyaki chicken": [MEAL]})
    _mock(monkeypatch, handler)

    mine = mealdb.search("teriyaki chicken")
    theirs = normalize_jsonld({"@type": "Recipe", "name": "x"})

    assert set(mine) - set(theirs) == {"sourceUrl"}
    assert set(theirs) - set(mine) == set()
