"""AI nutrition estimation: the sanitizer and the estimate endpoint."""
import app.api.ai as ai_api
from app.services.ai.nutrition import estimate_nutrition, sanitize


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, prompt, system=None, max_tokens=None):
        return self.payload


# --- sanitize ---------------------------------------------------------------

def test_sanitize_keeps_only_known_numeric_fields():
    out = sanitize({"calories": "520", "protein": 28, "junk": 9, "steps": ["x"]})
    assert out == {"calories": 520.0, "protein": 28.0}


def test_sanitize_drops_negative_and_nonnumeric():
    assert sanitize({"calories": -5, "fat": "lots", "sugar": None}) == {}


def test_sanitize_drops_non_finite_values():
    # inf/-inf/nan would serialize as invalid JSON (Infinity/NaN) and poison the
    # stored recipe, breaking every later fetch. They must be dropped.
    assert sanitize({"calories": float("inf"), "protein": float("-inf"),
                     "fat": float("nan")}) == {}


def test_sanitize_handles_non_dict():
    assert sanitize(["not", "a", "dict"]) == {}


def test_estimate_nutrition_returns_sanitized():
    out = estimate_nutrition(["2 eggs"], 4, FakeProvider({"calories": 90, "bad": 1}))
    assert out == {"calories": 90.0}


# --- endpoint ---------------------------------------------------------------

def _make_recipe(client, ingredients=("2 eggs", "1 cup flour")):
    rid = client.post("/api/v1/recipes", json={"name": "Test"}).get_json()["id"]
    client.put(f"/api/v1/recipes/{rid}",
               json={"servings": 4,
                     "ingredients": [{"display": d, "position": i}
                                     for i, d in enumerate(ingredients)]})
    return rid


def test_estimate_nutrition_stores_and_returns(auth_client, monkeypatch):
    monkeypatch.setattr(ai_api, "get_provider",
                        lambda: FakeProvider({"calories": 300, "protein": 12}))
    rid = _make_recipe(auth_client)
    r = auth_client.post(f"/api/v1/ai/nutrition/{rid}")
    assert r.status_code == 200
    assert r.get_json()["nutrition"] == {"calories": 300.0, "protein": 12.0}
    # Persisted: the recipe now serializes the stored nutrition.
    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["nutrition"]
    assert got["calories"] == 300.0


def test_estimate_nutrition_422_without_ingredients(auth_client, monkeypatch):
    monkeypatch.setattr(ai_api, "get_provider", lambda: FakeProvider({}))
    rid = auth_client.post("/api/v1/recipes", json={"name": "Empty"}).get_json()["id"]
    assert auth_client.post(f"/api/v1/ai/nutrition/{rid}").status_code == 422


def test_estimate_nutrition_404_for_missing_recipe(auth_client, monkeypatch):
    monkeypatch.setattr(ai_api, "get_provider", lambda: FakeProvider({}))
    assert auth_client.post("/api/v1/ai/nutrition/nope").status_code == 404
