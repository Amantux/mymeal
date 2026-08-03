"""The import pipeline: streamed stages, and what happens with nothing configured.

The single most important property here is the last one anyone thinks to test:
with no AI provider and no search key, an import must behave exactly as it did
before any of this existed — same result, zero model calls, zero web calls.
"""
import json

import pytest

import app.api.ai as ai_api
from app.services import conversions
from app.services.ai import recipe_import

JSONLD_PAGE = """
<html><head><script type="application/ld+json">
{"@type": "Recipe", "name": "Test Soup", "recipeYield": "4",
 "recipeIngredient": ["250 g flour", "a good handful of parsley"],
 "recipeInstructions": ["Boil it."]}
</script></head><body></body></html>
"""


class Exploder:
    """Any call to this is a test failure: it proves a network round-trip that
    was supposed to be skipped."""

    def __init__(self, label):
        self.label = label

    def __getattr__(self, name):
        def fail(*a, **k):
            pytest.fail(f"{self.label}.{name} was called but must not have been")
        return fail


class Structurer:
    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt, system="", max_tokens=4096):
        self.calls += 1
        return {"items": [{"index": 0, "quantity": 30, "unit": "g",
                           "food": "parsley", "confidence": 0.9}]}


@pytest.fixture()
def jsonld(monkeypatch):
    monkeypatch.setattr(recipe_import, "_fetch", lambda url: JSONLD_PAGE)


def events(response):
    return [json.loads(line) for line in
            response.get_data(as_text=True).splitlines() if line.strip()]


# --- the no-provider path -----------------------------------------------------

def test_with_nothing_configured_an_import_makes_no_model_or_web_calls(
    auth_client, jsonld, monkeypatch
):
    from app.services.ai.base import ProviderError

    def no_provider():
        raise ProviderError("none configured")

    monkeypatch.setattr(ai_api, "get_provider", no_provider)
    monkeypatch.setattr(ai_api.ingredient_ai, "structure",
                        Exploder("ingredient_ai").structure)
    monkeypatch.setattr(conversions, "websearch", Exploder("websearch"))

    r = auth_client.post("/api/v1/ai/import", json={"url": "https://x.com/soup"})

    assert r.status_code == 201
    body = r.get_json()
    assert body["name"] == "Test Soup"
    assert len(body["ingredients"]) == 2
    assert body["ingredientProposals"] == []


def test_a_recipe_the_parser_fully_reads_never_reaches_the_model(
    auth_client, monkeypatch
):
    """The cost of this feature must be proportional to how messy the recipe is."""
    clean = JSONLD_PAGE.replace('"a good handful of parsley"', '"2 tbsp olive oil"')
    monkeypatch.setattr(recipe_import, "_fetch", lambda url: clean)
    provider = Structurer()
    monkeypatch.setattr(ai_api, "get_provider", lambda: provider)
    monkeypatch.setattr(conversions, "websearch", Exploder("websearch"))

    r = auth_client.post("/api/v1/ai/import", json={"url": "https://x.com/soup"})

    assert r.status_code == 201
    assert provider.calls == 0


# --- proposals ----------------------------------------------------------------

def test_an_unreadable_line_comes_back_as_a_proposal_not_a_rewrite(
    auth_client, jsonld, monkeypatch
):
    provider = Structurer()
    monkeypatch.setattr(ai_api, "get_provider", lambda: provider)
    monkeypatch.setattr(conversions, "websearch", Exploder("websearch"))

    body = auth_client.post(
        "/api/v1/ai/import", json={"url": "https://x.com/soup"}
    ).get_json()

    assert provider.calls == 1
    [proposal] = body["ingredientProposals"]
    assert proposal["food"] == "parsley"
    assert proposal["quantity"] == 30
    # The saved line is untouched — the proposal is a suggestion awaiting a human.
    saved = [i["display"] for i in body["ingredients"]]
    assert "a good handful of parsley" in saved


def test_a_failing_model_does_not_fail_the_import(auth_client, jsonld, monkeypatch):
    class Broken:
        def complete_json(self, *a, **k):
            raise RuntimeError("model died")

    monkeypatch.setattr(ai_api, "get_provider", lambda: Broken())
    monkeypatch.setattr(conversions, "websearch", Exploder("websearch"))

    r = auth_client.post("/api/v1/ai/import", json={"url": "https://x.com/soup"})

    assert r.status_code == 201
    assert r.get_json()["ingredientProposals"] == []


def test_a_conversion_lookup_failure_never_loses_the_recipe(
    auth_client, jsonld, monkeypatch
):
    """The lookup is a network call that happens AFTER the save. If it can take
    the recipe down with it, the user loses work over an optional feature."""
    from app.services.ai.base import ProviderError

    def no_provider():
        raise ProviderError("none configured")

    def boom(*a, **k):
        raise RuntimeError("search exploded")

    monkeypatch.setattr(ai_api, "get_provider", no_provider)
    monkeypatch.setattr(ai_api.conversions, "learn_for_lines", boom)

    r = auth_client.post("/api/v1/ai/import", json={"url": "https://x.com/soup"})

    assert r.status_code == 201
    assert r.get_json()["name"] == "Test Soup"
    assert auth_client.get("/api/v1/recipes").get_json()["total"] == 1


# --- streaming ----------------------------------------------------------------

def test_the_stream_reports_stages_in_order_and_ends_with_the_recipe(
    auth_client, jsonld, monkeypatch
):
    provider = Structurer()
    monkeypatch.setattr(ai_api, "get_provider", lambda: provider)
    monkeypatch.setattr(conversions, "websearch", Exploder("websearch"))

    r = auth_client.post("/api/v1/ai/import/stream",
                         json={"url": "https://x.com/soup"})

    assert r.status_code == 200
    # Load-bearing under ingress: without it the proxy buffers the whole
    # response and every stage arrives at once, at the end.
    assert r.headers["X-Accel-Buffering"] == "no"
    assert r.mimetype == "application/x-ndjson"

    got = events(r)
    stages = [e["stage"] for e in got if e["type"] == "stage"]
    assert stages == ["fetching", "parsing", "structuring", "converting"]
    assert got[-1]["type"] == "done"
    assert got[-1]["name"] == "Test Soup"


def test_a_stream_error_is_an_event_not_a_raise(auth_client, monkeypatch):
    """Headers are already sent by the time this can fail; raising would leave
    the client hanging on a truncated body."""
    r = auth_client.post("/api/v1/ai/import/stream", json={})

    assert r.status_code == 200, "the failure is in the body, not the status"
    [event] = events(r)
    assert event["type"] == "error"
    assert event["status"] == 422


def test_the_stream_still_requires_a_login(client):
    assert client.post("/api/v1/ai/import/stream", json={}).status_code == 401


def test_an_unexpected_stream_failure_does_not_echo_the_exception(
    auth_client, jsonld, monkeypatch
):
    """An exception here can carry a DSN or a provider's response body, and this
    text goes straight to the browser."""
    def boom(*a, **k):
        raise RuntimeError("postgresql://mymeal:hunter2@db:5432/mymeal is down")

    monkeypatch.setattr(ai_api, "_import_events", boom)

    r = auth_client.post("/api/v1/ai/import/stream", json={"url": "https://x.com/s"})

    [event] = events(r)
    assert event["type"] == "error"
    assert "hunter2" not in event["error"]
    assert "postgresql" not in event["error"]
