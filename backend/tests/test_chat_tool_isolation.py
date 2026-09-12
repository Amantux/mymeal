"""Each chat tool runs in its own SAVEPOINT.

Spec (docs/chat-interface-spec.md §8): "A tool failure MUST be caught and fed
back to the model as ``{"error": …}`` — it MUST NOT 500 the turn." Without a
per-tool SAVEPOINT a tool that writes and *then* fails leaves the session in a
failed state, so the error handback is cosmetic: every later tool and the
request's own commit die with PendingRollbackError and the turn 500s.
"""
from app.services.ai.base import AIProvider, ChatResult, ToolCall


class _Fake(AIProvider):
    name = "fake"

    def available(self):
        return True

    def _complete(self, system, prompt, max_tokens):
        return "{}"


def _boom_then_add(monkeypatch):
    """Patch in a tool that half-writes then raises, keeping the real rest."""
    import app.services.ai.agent as agent
    from app.extensions import db
    from app.models import ShoppingList

    real = agent.execute_tool

    def flaky(gid, name, args):
        if name == "boom":
            db.session.add(ShoppingList(name="ghost", group_id=gid))
            db.session.flush()
            raise RuntimeError("tool exploded after writing")
        return real(gid, name, args)

    monkeypatch.setattr(agent, "execute_tool", flaky)


class BoomThenAdd(_Fake):
    """Calls the exploding tool, then a real writing tool, then answers."""

    def chat(self, messages, system="", tools=None, max_tokens=2048):
        txt = " ".join((m.get("content") or "") for m in messages)
        if "Result of boom" not in txt:
            return ChatResult(tool_calls=[ToolCall(id="c1", name="boom", arguments={})])
        if "Result of add_to_shopping_list" not in txt:
            return ChatResult(
                tool_calls=[
                    ToolCall(
                        id="c2", name="add_to_shopping_list", arguments={"item": "milk"}
                    )
                ]
            )
        return ChatResult(content="Recovered and added milk.")

    def chat_stream(self, messages, system="", tools=None, max_tokens=2048):
        yield {"type": "final", "result": self.chat(messages, system, tools)}


def test_failing_tool_does_not_500_the_turn(auth_client, monkeypatch):
    import app.api.chat as chat_api

    _boom_then_add(monkeypatch)
    monkeypatch.setattr(chat_api, "get_provider", lambda: BoomThenAdd())

    r = auth_client.post("/api/v1/ai/chat", json={"message": "go"})

    assert r.status_code == 200, r.get_data(as_text=True)[:400]


def test_failing_tool_is_fed_back_as_error_and_later_tools_still_work(
    auth_client, monkeypatch
):
    import app.api.chat as chat_api

    _boom_then_add(monkeypatch)
    monkeypatch.setattr(chat_api, "get_provider", lambda: BoomThenAdd())

    body = auth_client.post("/api/v1/ai/chat", json={"message": "go"}).get_json()

    assert body["trace"][0]["result"]["error"]
    assert body["trace"][1]["result"]["added"] == "milk"


def test_failing_tools_partial_write_is_rolled_back(auth_client, monkeypatch):
    import app.api.chat as chat_api

    _boom_then_add(monkeypatch)
    monkeypatch.setattr(chat_api, "get_provider", lambda: BoomThenAdd())

    auth_client.post("/api/v1/ai/chat", json={"message": "go"})

    lists = auth_client.get("/api/v1/shopping-lists").get_json()["items"]
    assert not any(sl["name"] == "ghost" for sl in lists)


def _integrity_error_tool(monkeypatch):
    """Patch in a tool whose own flush fails — the case that poisons the session."""
    import app.services.ai.agent as agent
    from app.extensions import db
    from app.models import ShoppingList

    real = agent.execute_tool

    def flaky(gid, name, args):
        if name == "boom":
            db.session.add(ShoppingList(name="orphan", group_id=None))
            db.session.flush()  # IntegrityError: group_id is NOT NULL
            return {"ok": True}
        return real(gid, name, args)

    monkeypatch.setattr(agent, "execute_tool", flaky)


def test_tool_whose_flush_fails_does_not_500_the_turn(auth_client, monkeypatch):
    import app.api.chat as chat_api

    _integrity_error_tool(monkeypatch)
    monkeypatch.setattr(chat_api, "get_provider", lambda: BoomThenAdd())

    r = auth_client.post("/api/v1/ai/chat", json={"message": "go"})

    assert r.status_code == 200, r.get_data(as_text=True)[:400]


def test_tool_whose_flush_fails_still_lets_later_tools_write(auth_client, monkeypatch):
    import app.api.chat as chat_api

    _integrity_error_tool(monkeypatch)
    monkeypatch.setattr(chat_api, "get_provider", lambda: BoomThenAdd())

    auth_client.post("/api/v1/ai/chat", json={"message": "go"})

    lists = auth_client.get("/api/v1/shopping-lists").get_json()["items"]
    assert any(i["display"] == "milk" for sl in lists for i in sl["items"])


def test_streaming_failing_tool_does_not_abort_the_stream(auth_client, monkeypatch):
    import app.api.chat as chat_api

    _boom_then_add(monkeypatch)
    monkeypatch.setattr(chat_api, "get_provider", lambda: BoomThenAdd())

    r = auth_client.post("/api/v1/ai/chat/stream", json={"message": "go"})
    events = [line for line in r.get_data(as_text=True).splitlines() if line.strip()]

    assert '"type": "done"' in events[-1], events[-1][:300]
