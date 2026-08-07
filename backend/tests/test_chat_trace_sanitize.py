"""A tool exception must not leak raw internals into the chat trace.

The trace is returned to the browser (POST /ai/chat -> {"trace": ...}) and
persisted as tool_trace, then served forever. An ORM error's str() carries the
SQL statement and bound parameters; a connection error carries the DSN with
password. Neither may reach the client verbatim.
"""
import json

from app.services.ai import agent
from app.services.ai.base import ChatResult, ToolCall


class _FakeProvider:
    """One turn that calls a tool, then (after the tool errors) replies."""
    def __init__(self):
        self.calls = 0

    def chat(self, messages, system=None, tools=None):
        self.calls += 1
        if self.calls == 1:
            return ChatResult(content="", tool_calls=[ToolCall(
                    id="1", name="search_recipes", arguments={"q": "x"})])
        return ChatResult(content="done", tool_calls=[])


def test_a_tool_exception_is_sanitized_in_the_trace(monkeypatch, app):
    leak = ("(psycopg2.OperationalError) connection to server failed: "
            "password authentication failed for user; "
            "[SQL: SELECT * FROM recipes WHERE secret=%(x)s] "
            "[parameters: {'x': 'sk-abcdefghijklmnopqrstuvwxyz012345'}]")

    def boom(gid, name, args):
        raise RuntimeError(leak)

    monkeypatch.setattr(agent, "execute_tool", boom)

    with app.app_context():
        result = agent.run_chat(
            gid="g", provider=_FakeProvider(), history=[],
            user_message="find something")

    blob = json.dumps(result["trace"])
    assert "[SQL:" not in blob and "parameters:" not in blob, \
        "the SQL statement / bound parameters leaked into the trace"
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in blob, "a secret leaked"
    assert "password authentication failed" not in blob or "[redacted]" in blob
    # The error is still REPORTED (the model needs to know the tool failed),
    # just not with the raw internals.
    assert any("search_recipes failed" in json.dumps(s) for s in result["trace"])
