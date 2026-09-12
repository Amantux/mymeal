"""Chat turns record a metric sample, like Edibl's ChatTimer does.

The log line is the authoritative record (it spans workers and restarts); the
in-process ring is what these tests read back.
"""
from app.services import metrics
from app.services.ai.base import AIProvider, ChatResult, ProviderError, ToolCall


class _Answer(AIProvider):
    name = "fake"

    def available(self):
        return True

    def _complete(self, system, prompt, max_tokens):
        return "{}"

    def chat(self, messages, system="", tools=None, max_tokens=2048):
        return ChatResult(content="hi there")

    def chat_stream(self, messages, system="", tools=None, max_tokens=2048):
        yield {"type": "delta", "text": "hi "}
        yield {"type": "delta", "text": "there"}
        yield {"type": "final", "result": ChatResult(content="hi there")}


class _OneTool(_Answer):
    def chat(self, messages, system="", tools=None, max_tokens=2048):
        if not any("Result of search_recipes" in (m.get("content") or "") for m in messages):
            return ChatResult(
                tool_calls=[ToolCall(id="c1", name="search_recipes",
                                     arguments={"query": "x"})]
            )
        return ChatResult(content="none found")


def _last_chat_sample():
    samples = metrics.recent("chat")
    return samples[-1] if samples else None


def test_post_turn_records_a_chat_metric(auth_client, monkeypatch):
    import app.api.chat as chat_api

    monkeypatch.setattr(chat_api, "get_provider", lambda: _Answer())

    auth_client.post("/api/v1/ai/chat", json={"message": "hi"})

    sample = _last_chat_sample()
    assert sample["kind"] == "chat" and sample["mode"] == "post"


def test_post_turn_metric_records_provider_and_ok(auth_client, monkeypatch):
    import app.api.chat as chat_api

    monkeypatch.setattr(chat_api, "get_provider", lambda: _Answer())

    auth_client.post("/api/v1/ai/chat", json={"message": "hi"})

    sample = _last_chat_sample()
    assert sample["provider"] == "fake" and sample["ok"] is True


def test_post_turn_metric_counts_tool_rounds(auth_client, monkeypatch):
    import app.api.chat as chat_api

    monkeypatch.setattr(chat_api, "get_provider", lambda: _OneTool())

    auth_client.post("/api/v1/ai/chat", json={"message": "hi"})

    assert _last_chat_sample()["toolRounds"] == 1


def test_provider_failure_records_ok_false(auth_client, monkeypatch):
    """The exception is handled in the view, so ok must be set explicitly."""
    import app.api.chat as chat_api

    class Fails(_Answer):
        def chat(self, messages, system="", tools=None, max_tokens=2048):
            raise ProviderError("upstream died")

    monkeypatch.setattr(chat_api, "get_provider", lambda: Fails())

    r = auth_client.post("/api/v1/ai/chat", json={"message": "hi"})

    assert r.status_code == 502
    assert _last_chat_sample()["ok"] is False


def test_stream_turn_records_mode_stream_and_ttft(auth_client, monkeypatch):
    import app.api.chat as chat_api

    monkeypatch.setattr(chat_api, "get_provider", lambda: _Answer())

    auth_client.post("/api/v1/ai/chat/stream", json={"message": "hi"})

    sample = _last_chat_sample()
    assert sample["mode"] == "stream" and isinstance(sample["ttftMs"], int)


def test_provider_without_a_name_still_completes_the_turn(auth_client, monkeypatch):
    """The loop needs only .chat from a provider, so the metric must not demand
    more than that — reading provider.name eagerly once 500'd these turns."""
    import app.api.chat as chat_api

    class Duck:  # deliberately not an AIProvider subclass
        def chat(self, messages, system=None, tools=None):
            return ChatResult(content="hi")

    monkeypatch.setattr(chat_api, "get_provider", lambda: Duck())

    r = auth_client.post("/api/v1/ai/chat", json={"message": "hi"})

    assert r.status_code == 200


def test_metrics_failure_never_breaks_a_chat_turn(auth_client, monkeypatch):
    import app.api.chat as chat_api

    monkeypatch.setattr(chat_api, "get_provider", lambda: _Answer())
    monkeypatch.setattr(
        metrics, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    r = auth_client.post("/api/v1/ai/chat", json={"message": "hi"})

    assert r.status_code == 200
