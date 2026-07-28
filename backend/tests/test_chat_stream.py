"""Streaming chat (/ai/chat/stream): NDJSON deltas + terminal done, same tool-loop
and single-commit persistence as /ai/chat, plus the household streaming default."""
import json

from app.services.ai.base import ChatResult, ToolCall


class _FakeStreamProvider:
    """First turn asks for a tool (no text); second turn streams the answer."""
    name = "fakestream"

    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def chat(self, messages, system="", tools=None, max_tokens=2048):  # not used by stream
        return ChatResult(content="(unused)")

    def chat_stream(self, messages, system="", tools=None, max_tokens=2048):
        self.calls += 1
        if self.calls == 1:
            yield {"type": "final", "result": ChatResult(
                tool_calls=[ToolCall(id="1", name="find_recipes", arguments={"query": "eggs"})])}
            return
        for piece in ["You can make ", "an omelette."]:
            yield {"type": "delta", "text": piece}
        yield {"type": "final", "result": ChatResult(content="You can make an omelette.")}


def _lines(resp):
    return [json.loads(ln) for ln in resp.get_data(as_text=True).splitlines() if ln.strip()]


def test_chat_stream_emits_deltas_then_terminal_done(auth_client, monkeypatch):
    monkeypatch.setattr("app.api.chat.get_provider", lambda: _FakeStreamProvider())

    r = auth_client.post("/api/v1/ai/chat/stream", json={"message": "what can I cook with eggs?"})
    assert r.status_code == 200

    events = _lines(r)
    types = [e["type"] for e in events]
    assert "tool" in types            # a tool round ran
    assert types.count("delta") >= 1  # the answer streamed in pieces
    done = events[-1]
    assert done["type"] == "done"
    assert "omelette" in done["reply"].lower() and done["sessionId"]

    # Same single-commit persistence as the non-streaming endpoint.
    convo = auth_client.get(f"/api/v1/ai/chat/sessions/{done['sessionId']}").get_json()
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant"]


def test_chat_stream_requires_auth(client):
    assert client.post("/api/v1/ai/chat/stream", json={"message": "hi"}).status_code == 401


def test_chat_streaming_default_is_post_and_owner_can_set(auth_client):
    assert auth_client.get("/api/v1/ai/chat-settings").get_json()["stream"] is False
    assert auth_client.put("/api/v1/ai/chat-settings", json={"stream": True}).get_json()["stream"] is True
    assert auth_client.get("/api/v1/ai/chat-settings").get_json()["stream"] is True
