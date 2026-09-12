"""The chat's get_recipe tool asks instead of guessing.

Resolution policy lives in services.recipe_resolve; the chat tool must consume
it rather than taking the first ilike hit, or "the chicken one" confidently
recites the wrong recipe's ingredients and steps.
"""
from app.services.ai.base import AIProvider, ChatResult, ToolCall


def _make_recipe(client, name, **kw):
    return client.post(
        "/api/v1/recipes",
        json={"name": name, "ingredients": [{"display": "x"}], **kw},
    ).get_json()


class _GetRecipe(AIProvider):
    """Calls get_recipe once with a fixed reference, then reports the result."""

    name = "fake"
    ref = "chicken"

    def available(self):
        return True

    def _complete(self, system, prompt, max_tokens):
        return "{}"

    def chat(self, messages, system="", tools=None, max_tokens=2048):
        if not any("Result of get_recipe" in (m.get("content") or "") for m in messages):
            return ChatResult(
                tool_calls=[
                    ToolCall(id="c1", name="get_recipe",
                             arguments={"name_or_id": self.ref})
                ]
            )
        return ChatResult(content="done")


def _trace(auth_client, monkeypatch, ref):
    import app.api.chat as chat_api

    provider = _GetRecipe()
    provider.ref = ref
    monkeypatch.setattr(chat_api, "get_provider", lambda: provider)
    body = auth_client.post("/api/v1/ai/chat", json={"message": "go"}).get_json()
    return body["trace"][0]["result"]


def test_get_recipe_ambiguous_name_asks_instead_of_guessing(auth_client, monkeypatch):
    _make_recipe(auth_client, "Chicken Soup")
    _make_recipe(auth_client, "Chicken Curry")

    result = _trace(auth_client, monkeypatch, "chicken")

    assert result["needsClarification"] is True
    assert {c["name"] for c in result["candidates"]} == {"Chicken Soup", "Chicken Curry"}


def test_get_recipe_ambiguous_name_returns_no_recipe_body(auth_client, monkeypatch):
    _make_recipe(auth_client, "Chicken Soup")
    _make_recipe(auth_client, "Chicken Curry")

    result = _trace(auth_client, monkeypatch, "chicken")

    # Nothing was looked up — no ingredients/steps for the model to recite.
    assert "ingredients" not in result and "steps" not in result


def test_get_recipe_unique_exact_name_still_resolves(auth_client, monkeypatch):
    _make_recipe(auth_client, "Chicken Soup")
    _make_recipe(auth_client, "Beef Stew")

    result = _trace(auth_client, monkeypatch, "Chicken Soup")

    assert result["name"] == "Chicken Soup"


def test_get_recipe_by_id_is_never_refuzzed(auth_client, monkeypatch):
    """An id the model already holds is an unambiguous handle."""
    rid = _make_recipe(auth_client, "Chicken Soup")["id"]
    _make_recipe(auth_client, "Chicken Curry")

    result = _trace(auth_client, monkeypatch, rid)

    assert result["name"] == "Chicken Soup"


def test_get_recipe_sole_weak_match_resolves(auth_client, monkeypatch):
    """One substring hit is not ambiguity — there is nothing to confuse it with."""
    _make_recipe(auth_client, "Butter Chicken")
    _make_recipe(auth_client, "Beef Stew")

    result = _trace(auth_client, monkeypatch, "chicken")

    assert result["name"] == "Butter Chicken"


def test_get_recipe_no_match_reports_no_matching_recipe(auth_client, monkeypatch):
    _make_recipe(auth_client, "Beef Stew")

    result = _trace(auth_client, monkeypatch, "sushi")

    assert result["error"] == "no matching recipe"


def test_search_recipes_still_lists_all_matches_without_asking(auth_client, monkeypatch):
    """A summarising read must NOT disambiguate — that would be a regression."""
    import app.api.chat as chat_api

    _make_recipe(auth_client, "Chicken Soup")
    _make_recipe(auth_client, "Chicken Curry")

    class Search(_GetRecipe):
        def chat(self, messages, system="", tools=None, max_tokens=2048):
            if not any(
                "Result of search_recipes" in (m.get("content") or "") for m in messages
            ):
                return ChatResult(
                    tool_calls=[
                        ToolCall(id="c1", name="search_recipes",
                                 arguments={"query": "chicken"})
                    ]
                )
            return ChatResult(content="done")

    monkeypatch.setattr(chat_api, "get_provider", lambda: Search())
    body = auth_client.post("/api/v1/ai/chat", json={"message": "go"}).get_json()

    assert len(body["trace"][0]["result"]) == 2


def test_get_recipe_does_not_cross_group_boundaries(app, monkeypatch):
    """Another group's identically-named recipe is neither matched nor offered."""
    import app.api.chat as chat_api
    from unittest.mock import patch

    c = app.test_client()
    c.post("/api/v1/users/register",
           json={"email": "a@x.com", "password": "pw12345", "name": "A"})
    ta = c.post("/api/v1/users/login",
                json={"username": "a@x.com", "password": "pw12345"}).get_json()["token"]
    c.post("/api/v1/recipes", json={"name": "Chicken Soup",
                                    "ingredients": [{"display": "x"}]},
           headers={"Authorization": ta})

    c.post("/api/v1/users/register",
           json={"email": "b@x.com", "password": "pw12345", "name": "B"})
    tb = c.post("/api/v1/users/login",
                json={"username": "b@x.com", "password": "pw12345"}).get_json()["token"]

    with patch.object(chat_api, "get_provider", lambda: _GetRecipe()):
        body = c.post("/api/v1/ai/chat", json={"message": "go"},
                      headers={"Authorization": tb}).get_json()

    assert body["trace"][0]["result"]["error"] == "no matching recipe"
