"""myMeal's chat can manage the Edibl pantry — but ONLY when Edibl is connected.
Standalone myMeal never advertises those tools, so the two apps have no
co-deployment dependency. Cross-app mutations surface as chips with no undo
(myMeal's undo runs in the browser, which can't reach Edibl)."""
from app.services.ai import agent
from app.services.ai.agent import actions_from_trace


class _StubClient:
    def __init__(self, configured=True):
        self.configured = configured

    def have(self, ingredient):
        return {"ok": True, "data": {"ingredient": ingredient, "have": True}}

    def get_stock(self):
        return {"ok": True, "items": [{"name": "Milk", "quantity": 2, "unit": "L"}]}

    def expiring(self, days):
        # Edibl returns a BARE LIST here — the tool must handle it, not crash.
        return {"ok": True, "data": [{"name": "Yogurt", "daysToExpiry": 2}]}

    def add_stock(self, name, **kw):
        return {"ok": True, "data": {"id": "lot-1"}}

    def find_lot(self, name):
        return {"id": "lot-9", "product": {"name": name}}

    def consume(self, lot_id, **kw):
        return {"ok": True, "data": {"consumptionId": "ce-1",
                                     "consumedAmount": kw.get("quantity", 1)}}

    def unconsume(self, lot_id, consumption_id=None, amount=0):
        return {"ok": True}

    def add_shopping(self, name, **kw):
        return {"ok": True, "data": {"id": "si-1"}}

    def delete_shopping(self, item_id):
        return {"ok": True}


def _use_stub(monkeypatch, configured=True):
    monkeypatch.setattr(agent.EdiblClient, "from_settings",
                        classmethod(lambda cls, *a, **k: _StubClient(configured)))


def test_edibl_tools_hidden_when_not_connected(monkeypatch):
    monkeypatch.setattr(agent, "_edibl_connected", lambda: False)
    assert agent._sibling_tools() == []


def test_edibl_tools_present_when_connected(monkeypatch):
    monkeypatch.setattr(agent, "_edibl_connected", lambda: True)
    names = {t["name"] for t in agent._sibling_tools()}
    assert {"edibl_add_stock", "edibl_do_i_have", "edibl_record_consumption"} <= names


def test_edibl_add_stock_chip_is_undoable_via_proxy(monkeypatch):
    _use_stub(monkeypatch)
    res = agent.execute_tool("gid", "edibl_add_stock",
                             {"name": "Milk", "quantity": 2, "unit": "L"})
    assert res["added"] == "Milk" and res["lotId"] == "lot-1"
    chip = actions_from_trace(
        [{"tool": "edibl_add_stock", "args": {}, "result": res}])[0]
    assert chip["kind"] == "stock" and "Milk" in chip["label"]
    # The chip carries a server-reversible undo descriptor (the browser can't
    # reach Edibl; POST /ai/chat/undo does the delete).
    assert chip["undo"] == {"kind": "edibl_stock", "id": "lot-1"}


def test_edibl_add_stock_chip_has_no_undo_without_lot_id(monkeypatch):
    """If Edibl didn't return a lot id, no undo is offered (nothing to reverse)."""
    class _NoId(_StubClient):
        def add_stock(self, name, **kw):
            return {"ok": True, "data": {}}
    monkeypatch.setattr(agent.EdiblClient, "from_settings",
                        classmethod(lambda cls, *a, **k: _NoId()))
    res = agent.execute_tool("gid", "edibl_add_stock", {"name": "Milk"})
    chip = actions_from_trace(
        [{"tool": "edibl_add_stock", "args": {}, "result": res}])[0]
    assert "undo" not in chip


def test_edibl_consumption_chip_is_undoable(monkeypatch):
    _use_stub(monkeypatch)
    res = agent.execute_tool("gid", "edibl_record_consumption",
                             {"name": "Milk", "quantity": 1, "outcome": "eaten"})
    assert res["consumed"] == "Milk"
    chip = actions_from_trace(
        [{"tool": "edibl_record_consumption", "args": {}, "result": res}])[0]
    assert chip["kind"] == "consume"
    assert chip["undo"] == {"kind": "edibl_unconsume", "lotId": "lot-9",
                            "consumptionId": "ce-1", "amount": 1}


def test_edibl_shopping_chip_is_undoable(monkeypatch):
    _use_stub(monkeypatch)
    res = agent.execute_tool("gid", "edibl_add_to_shopping", {"name": "Butter"})
    assert res["addedToShopping"] == "Butter" and res["shoppingId"] == "si-1"
    chip = actions_from_trace(
        [{"tool": "edibl_add_to_shopping", "args": {}, "result": res}])[0]
    assert chip["undo"] == {"kind": "edibl_shopping", "id": "si-1"}


def test_edibl_tool_degrades_when_not_configured(monkeypatch):
    _use_stub(monkeypatch, configured=False)
    res = agent.execute_tool("gid", "edibl_do_i_have", {"ingredient": "eggs"})
    assert res.get("available") is False


def test_edibl_expiring_handles_bare_list(monkeypatch):
    """Regression: Edibl's /dashboard/expiring returns a bare list, not {items}."""
    _use_stub(monkeypatch)
    res = agent.execute_tool("gid", "edibl_expiring_soon", {"days": 3})
    assert res["items"] and res["items"][0]["name"] == "Yogurt"


# --- the server undo-proxy (browser can't reach Edibl, so the backend does) ---
def _make_app(tmp_path, name):
    from app import create_app
    return create_app(type("C", (), {
        "DATA_DIR": str(tmp_path), "DATABASE_URL": f"sqlite:///{tmp_path/name}",
        "MCP_ENABLED": False, "DISABLE_AUTH": True}))


def test_undo_endpoint_deletes_edibl_lot(monkeypatch, tmp_path):
    from app.services import edibl as edibl_mod
    deleted = {}

    class _Stub:
        configured = True

        def delete_stock(self, lot_id):
            deleted["id"] = lot_id
            return {"ok": True}

    monkeypatch.setattr(edibl_mod.EdiblClient, "from_settings",
                        classmethod(lambda cls, *a, **k: _Stub()))
    c = _make_app(tmp_path, "u.db").test_client()
    r = c.post("/api/v1/ai/chat/undo", json={"kind": "edibl_stock", "id": "lot-7"})
    assert r.status_code == 200 and r.get_json()["undone"] is True
    assert deleted["id"] == "lot-7"


def test_undo_endpoint_treats_404_as_undone(monkeypatch, tmp_path):
    from app.services import edibl as edibl_mod

    class _Gone:
        configured = True

        def delete_stock(self, lot_id):
            return {"ok": False, "reachable": True, "status": 404, "error": "HTTP 404"}

    monkeypatch.setattr(edibl_mod.EdiblClient, "from_settings",
                        classmethod(lambda cls, *a, **k: _Gone()))
    c = _make_app(tmp_path, "u404.db").test_client()
    r = c.post("/api/v1/ai/chat/undo", json={"kind": "edibl_stock", "id": "x"})
    assert r.status_code == 200 and r.get_json()["undone"] is True


def test_undo_endpoint_502_when_edibl_unreachable(monkeypatch, tmp_path):
    from app.services import edibl as edibl_mod

    class _Down:
        configured = True

        def delete_stock(self, lot_id):
            return {"ok": False, "reachable": False, "error": "timeout"}

    monkeypatch.setattr(edibl_mod.EdiblClient, "from_settings",
                        classmethod(lambda cls, *a, **k: _Down()))
    c = _make_app(tmp_path, "udown.db").test_client()
    r = c.post("/api/v1/ai/chat/undo", json={"kind": "edibl_stock", "id": "x"})
    assert r.status_code == 502 and r.get_json()["undone"] is False


def test_undo_endpoint_deletes_edibl_shopping(monkeypatch, tmp_path):
    from app.services import edibl as edibl_mod
    called = {}

    class _Stub:
        configured = True

        def delete_shopping(self, item_id):
            called["id"] = item_id
            return {"ok": True}

    monkeypatch.setattr(edibl_mod.EdiblClient, "from_settings",
                        classmethod(lambda cls, *a, **k: _Stub()))
    c = _make_app(tmp_path, "ushop.db").test_client()
    r = c.post("/api/v1/ai/chat/undo", json={"kind": "edibl_shopping", "id": "si-9"})
    assert r.status_code == 200 and r.get_json()["undone"] is True
    assert called["id"] == "si-9"


def test_undo_endpoint_reverses_consumption(monkeypatch, tmp_path):
    from app.services import edibl as edibl_mod
    called = {}

    class _Stub:
        configured = True

        def unconsume(self, lot_id, consumption_id=None, amount=0):
            called.update(lot=lot_id, event=consumption_id, amount=amount)
            return {"ok": True}

    monkeypatch.setattr(edibl_mod.EdiblClient, "from_settings",
                        classmethod(lambda cls, *a, **k: _Stub()))
    c = _make_app(tmp_path, "uncon.db").test_client()
    r = c.post("/api/v1/ai/chat/undo", json={
        "kind": "edibl_unconsume", "lotId": "lot-2",
        "consumptionId": "ce-2", "amount": 3})
    assert r.status_code == 200 and r.get_json()["undone"] is True
    assert called == {"lot": "lot-2", "event": "ce-2", "amount": 3}


def test_undo_endpoint_rejects_unknown_kind(tmp_path):
    c = _make_app(tmp_path, "ubad.db").test_client()
    r = c.post("/api/v1/ai/chat/undo", json={"kind": "nope", "id": "x"})
    assert r.status_code == 400


def test_undo_endpoint_rejects_path_traversal_id(monkeypatch, tmp_path):
    """An id must not be able to smuggle a path into the Edibl URL."""
    from app.services import edibl as edibl_mod
    called = {"n": 0}

    class _Stub:
        configured = True

        def delete_shopping(self, item_id):
            called["n"] += 1
            return {"ok": True}

    monkeypatch.setattr(edibl_mod.EdiblClient, "from_settings",
                        classmethod(lambda cls, *a, **k: _Stub()))
    c = _make_app(tmp_path, "utrav.db").test_client()
    r = c.post("/api/v1/ai/chat/undo",
               json={"kind": "edibl_shopping", "id": "../stock/victim"})
    assert r.status_code == 502 and called["n"] == 0  # rejected before any call
