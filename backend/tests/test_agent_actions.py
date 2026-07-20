"""The chat assistant surfaces structured actions (Edibl-style)."""
from app.services.ai.agent import actions_from_trace


def test_shopping_write_becomes_an_action_chip():
    trace = [{"tool": "add_to_shopping_list", "args": {"item": "eggs"},
              "result": {"added": "eggs", "list": "Shopping List"}}]
    actions = actions_from_trace(trace)
    assert len(actions) == 1
    assert actions[0]["kind"] == "shopping"
    assert "eggs" in actions[0]["label"]


def test_read_only_tools_produce_no_action():
    trace = [{"tool": "what_can_i_cook", "args": {}, "result": {"suggestions": []}},
             {"tool": "list_inventory", "args": {}, "result": {"items": []}}]
    assert actions_from_trace(trace) == []


def test_failed_write_produces_no_action():
    trace = [{"tool": "add_to_shopping_list", "args": {}, "result": {"error": "no item"}}]
    assert actions_from_trace(trace) == []


def test_chat_response_includes_actions_key(monkeypatch, tmp_path):
    """The /ai/chat response carries actions[] for the FAB to render."""
    from app import create_app
    import app.api.chat as chat_api

    class FakeProvider:
        def chat(self, messages, system=None, tools=None):
            class R:
                content = "Added it."
                tool_calls = []
            return R()

    app = create_app(type("C", (), {
        "DATA_DIR": str(tmp_path), "DATABASE_URL": f"sqlite:///{tmp_path/'c.db'}",
        "MCP_ENABLED": False, "DISABLE_AUTH": True}))
    monkeypatch.setattr(chat_api, "get_provider", lambda: FakeProvider())
    c = app.test_client()
    body = c.post("/api/v1/ai/chat", json={"message": "hi"}).get_json()
    assert "actions" in body and isinstance(body["actions"], list)


def test_shopping_action_carries_undo_descriptor():
    """The action must include a structured undo the client can reverse."""
    trace = [{"tool": "add_to_shopping_list", "args": {"item": "eggs"},
              "result": {"added": "eggs", "list": "Shopping List", "itemId": "abc-123"}}]
    action = actions_from_trace(trace)[0]
    assert action["undo"] == {"kind": "shopping_item", "id": "abc-123"}


def test_no_undo_when_item_id_missing():
    """Older result shape (no itemId) -> chip shown but no undo offered."""
    trace = [{"tool": "add_to_shopping_list", "args": {"item": "eggs"},
              "result": {"added": "eggs", "list": "Shopping List"}}]
    assert "undo" not in actions_from_trace(trace)[0]
