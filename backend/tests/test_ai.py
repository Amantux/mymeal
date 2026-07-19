"""AI provider layer + recipe import — no network or API keys required."""
import pytest

from app.services.ai import recipe_import
from app.services.ai.base import (
    extract_json,
    AIProvider,
    ChatResult,
    ProviderError,
)


# --- Pure helpers --------------------------------------------------------
def test_iso_duration_to_minutes():
    assert recipe_import._iso_duration_to_minutes("PT30M") == 30
    assert recipe_import._iso_duration_to_minutes("PT1H30M") == 90
    assert recipe_import._iso_duration_to_minutes("PT2H") == 120
    assert recipe_import._iso_duration_to_minutes("garbage") == 0
    assert recipe_import._iso_duration_to_minutes(None) == 0


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 2} done') == {"a": 2}
    assert extract_json('{"a": 3}') == {"a": 3}


JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Recipe","name":"Test Soup",
 "description":"A soup.","recipeYield":"4 servings",
 "prepTime":"PT10M","cookTime":"PT20M",
 "recipeIngredient":["2 carrots","1 onion"],
 "recipeInstructions":[{"@type":"HowToStep","text":"Chop."},
                       {"@type":"HowToStep","text":"Simmer."}]}
</script></head><body>page</body></html>
"""


def test_extract_jsonld_recipe():
    node = recipe_import.extract_jsonld_recipe(JSONLD_PAGE)
    payload = recipe_import.normalize_jsonld(node)
    assert payload["name"] == "Test Soup"
    assert payload["servings"] == 4
    assert payload["prepMinutes"] == 10
    assert payload["cookMinutes"] == 20
    assert payload["totalMinutes"] == 30  # derived from prep+cook
    assert len(payload["ingredients"]) == 2
    assert len(payload["steps"]) == 2


def test_import_recipe_url_uses_jsonld(monkeypatch):
    """A URL with JSON-LD imports deterministically, no provider needed."""
    monkeypatch.setattr(recipe_import, "_fetch", lambda url: JSONLD_PAGE)
    payload = recipe_import.import_recipe(url="https://example.com/soup")
    assert payload["name"] == "Test Soup"
    assert payload["sourceUrl"] == "https://example.com/soup"


class _FakeProvider(AIProvider):
    name = "fake"

    def available(self):
        return True

    def _complete(self, system, prompt, max_tokens):
        return (
            '{"name":"AI Pasta","servings":2,"totalMinutes":25,'
            '"ingredients":[{"display":"pasta"}],"steps":[{"text":"boil"}]}'
        )

    def chat(self, messages, system="", tools=None, max_tokens=2048):
        return ChatResult(content="ok")


def test_import_recipe_text_uses_provider():
    payload = recipe_import.import_recipe(
        text="some pasta recipe", provider=_FakeProvider()
    )
    assert payload["name"] == "AI Pasta"
    assert payload["servings"] == 2
    assert payload["ingredients"] == [{"display": "pasta"}]


# --- API -----------------------------------------------------------------
def test_ai_providers_endpoint(auth_client):
    res = auth_client.get("/api/v1/ai/providers").get_json()
    names = {p["name"] for p in res["providers"]}
    assert names == {"claude", "openai", "ollama"}


def test_ai_import_endpoint_jsonld(auth_client, monkeypatch):
    monkeypatch.setattr(recipe_import, "_fetch", lambda url: JSONLD_PAGE)
    r = auth_client.post("/api/v1/ai/import", json={"url": "https://x.com/soup"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["name"] == "Test Soup"
    assert body["slug"] == "test-soup"
    assert len(body["ingredients"]) == 2
    # It was actually saved to the group.
    assert auth_client.get("/api/v1/recipes").get_json()["total"] == 1


def test_ai_import_endpoint_text_with_provider(auth_client, monkeypatch):
    import app.api.ai as ai_api

    monkeypatch.setattr(ai_api, "get_provider", lambda: _FakeProvider())
    r = auth_client.post("/api/v1/ai/import", json={"text": "pasta"})
    assert r.status_code == 201
    assert r.get_json()["name"] == "AI Pasta"


def test_ai_import_text_without_provider_is_503(auth_client, monkeypatch):
    import app.api.ai as ai_api

    def _no_provider():
        raise ProviderError("none configured")

    monkeypatch.setattr(ai_api, "get_provider", _no_provider)
    r = auth_client.post("/api/v1/ai/import", json={"text": "pasta"})
    assert r.status_code == 503


# --- Adversarial / failure-path (regression for M2 review) ---------------
ARRAY_NAME_PAGE = """
<html><head><script type="application/ld+json">
{"@type":"Recipe","name":["Tacos","(v2)"],"description":["line1","line2"],
 "recipeIngredient":["tortillas",["nested","beef"]],
 "recipeInstructions":[{"text":["Warm","Serve"]}]}
</script></head><body>x</body></html>
"""


def test_jsonld_array_fields_degrade_not_crash():
    """schema.org fields as arrays/nested lists must coerce, not raise."""
    node = recipe_import.extract_jsonld_recipe(ARRAY_NAME_PAGE)
    payload = recipe_import.normalize_jsonld(node)
    assert payload["name"] == "Tacos (v2)"
    assert isinstance(payload["description"], str)
    assert all(isinstance(i["display"], str) for i in payload["ingredients"])
    assert all(isinstance(s["text"], str) for s in payload["steps"])


def test_extract_json_non_object_raises():
    for bad in ("[1,2,3]", "42", '"hi"', "not json at all"):
        with pytest.raises(ProviderError):
            extract_json(bad)


class _BadJsonProvider(_FakeProvider):
    def _complete(self, system, prompt, max_tokens):
        return "[1, 2, 3]"  # valid JSON, wrong shape


def test_import_recipe_bad_model_output_raises_provider_error():
    with pytest.raises(ProviderError):
        recipe_import.import_recipe(text="x", provider=_BadJsonProvider())


def test_ssrf_guard_blocks_private_and_nonhttp():
    for url in (
        "http://localhost/x",
        "http://127.0.0.1:8123/",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ):
        with pytest.raises(recipe_import.UnsafeURLError):
            recipe_import._assert_public_url(url)


def test_ai_import_non_string_url_is_not_500(auth_client):
    r = auth_client.post("/api/v1/ai/import", json={"url": 12345})
    # Coerced to a string, then rejected as an unsafe/invalid URL — never 500.
    assert r.status_code in (400, 502)


def test_ai_import_private_url_rejected(auth_client):
    r = auth_client.post(
        "/api/v1/ai/import", json={"url": "http://127.0.0.1:8123/config"}
    )
    assert r.status_code == 400
