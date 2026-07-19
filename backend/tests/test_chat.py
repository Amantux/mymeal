"""M4: conversational cooking agent — tool loop with a fake provider."""
from app.services.ai.base import AIProvider, ChatResult, ToolCall


def _make_recipe(client, name, ingredients):
    return client.post(
        "/api/v1/recipes",
        json={"name": name, "ingredients": [{"display": i} for i in ingredients]},
    ).get_json()


class ToolThenAnswerProvider(AIProvider):
    """First turn calls search_recipes; second turn answers in plain text.

    Uses the presence of a tool-result message in the history to decide which
    turn it's on, exercising the real tool loop end-to-end.
    """

    name = "fake"

    def available(self):
        return True

    def _complete(self, system, prompt, max_tokens):
        return "{}"

    def chat(self, messages, system="", tools=None, max_tokens=2048):
        already_called = any(
            "Result of search_recipes" in (m.get("content") or "") for m in messages
        )
        if not already_called:
            return ChatResult(
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="search_recipes", arguments={"query": "soup"})
                ],
            )
        return ChatResult(content="You have a Tomato Soup recipe.")


def test_chat_runs_tool_loop_and_persists(auth_client, monkeypatch):
    import app.api.chat as chat_api

    _make_recipe(auth_client, "Tomato Soup", ["tomato", "basil"])
    monkeypatch.setattr(chat_api, "get_provider", lambda: ToolThenAnswerProvider())

    r = auth_client.post("/api/v1/ai/chat", json={"message": "what soups do I have?"})
    assert r.status_code == 200
    body = r.get_json()
    assert "Tomato Soup" in body["reply"]
    # The tool was actually invoked against the group's data.
    assert body["trace"][0]["tool"] == "search_recipes"
    assert any(x["name"] == "Tomato Soup" for x in body["trace"][0]["result"])

    # Session + both messages persisted.
    sid = body["sessionId"]
    session = auth_client.get(f"/api/v1/ai/chat/sessions/{sid}").get_json()
    assert [m["role"] for m in session["messages"]] == ["user", "assistant"]

    # A follow-up in the same session carries history.
    r2 = auth_client.post(
        "/api/v1/ai/chat", json={"sessionId": sid, "message": "thanks"}
    )
    assert r2.status_code == 200
    session = auth_client.get(f"/api/v1/ai/chat/sessions/{sid}").get_json()
    assert len(session["messages"]) == 4


def test_chat_add_to_shopping_list_tool(auth_client, monkeypatch):
    import app.api.chat as chat_api

    class AddItemProvider(AIProvider):
        name = "fake"

        def available(self):
            return True

        def _complete(self, system, prompt, max_tokens):
            return "{}"

        def chat(self, messages, system="", tools=None, max_tokens=2048):
            if not any("Result of add_to_shopping_list" in (m.get("content") or "") for m in messages):
                return ChatResult(
                    tool_calls=[
                        ToolCall(id="c1", name="add_to_shopping_list", arguments={"item": "milk"})
                    ]
                )
            return ChatResult(content="Added milk to your list.")

    monkeypatch.setattr(chat_api, "get_provider", lambda: AddItemProvider())
    auth_client.post("/api/v1/ai/chat", json={"message": "add milk"})

    lists = auth_client.get("/api/v1/shopping-lists").get_json()["items"]
    assert any(
        i["display"] == "milk" for sl in lists for i in sl["items"]
    )


def test_chat_requires_provider(auth_client, monkeypatch):
    import app.api.chat as chat_api
    from app.services.ai.base import ProviderError

    def _none():
        raise ProviderError("no provider")

    monkeypatch.setattr(chat_api, "get_provider", _none)
    r = auth_client.post("/api/v1/ai/chat", json={"message": "hi"})
    assert r.status_code == 503


def test_chat_session_group_isolation(app):
    """One group cannot read another group's chat session."""
    import app.api.chat as chat_api
    from unittest.mock import patch

    c = app.test_client()
    c.post("/api/v1/users/register", json={"email": "g1@x.com", "password": "pw12345", "name": "G1"})
    t1 = c.post("/api/v1/users/login", json={"username": "g1@x.com", "password": "pw12345"}).get_json()["token"]

    class Echo(AIProvider):
        name = "fake"

        def available(self):
            return True

        def _complete(self, system, prompt, max_tokens):
            return "{}"

        def chat(self, messages, system="", tools=None, max_tokens=2048):
            return ChatResult(content="hi there")

    with patch.object(chat_api, "get_provider", lambda: Echo()):
        sid = c.post(
            "/api/v1/ai/chat", json={"message": "hello"}, headers={"Authorization": t1}
        ).get_json()["sessionId"]

    c.post("/api/v1/users/register", json={"email": "g2@x.com", "password": "pw12345", "name": "G2"})
    t2 = c.post("/api/v1/users/login", json={"username": "g2@x.com", "password": "pw12345"}).get_json()["token"]
    assert (
        c.get(f"/api/v1/ai/chat/sessions/{sid}", headers={"Authorization": t2}).status_code
        == 404
    )
