"""Edibl connection config: set via UI or HA, remembered, token write-only."""
import json


def test_config_reflects_env_default(noauth_app):
    app = noauth_app
    with app.app_context():
        app.config["SETTINGS"].values["EDIBL_URL"] = "http://envedibl:8099"
    body = app.test_client().get("/api/v1/edibl/config").get_json()
    assert body["url"] == "http://envedibl:8099"
    assert body["configured"] is True


def test_ui_config_overrides_env_and_persists(noauth_app):
    app = noauth_app
    with app.app_context():
        app.config["SETTINGS"].values["EDIBL_URL"] = "http://envedibl:8099"
    c = app.test_client()
    r = c.put("/api/v1/edibl/config", json={"url": "http://myedibl:8099", "token": "tok-abc"})
    assert r.status_code == 200 and r.get_json()["url"] == "http://myedibl:8099"
    assert c.get("/api/v1/edibl/config").get_json()["url"] == "http://myedibl:8099"


def test_token_is_never_returned(noauth_app):
    c = noauth_app.test_client()
    c.put("/api/v1/edibl/config", json={"url": "http://e:8099", "token": "sk-edibl-secret"})
    blob = json.dumps(c.get("/api/v1/edibl/config").get_json())
    assert "sk-edibl-secret" not in blob
    assert c.get("/api/v1/edibl/config").get_json()["tokenSet"] is True


def test_blank_token_on_resave_keeps_it(noauth_app):
    c = noauth_app.test_client()
    c.put("/api/v1/edibl/config", json={"url": "http://e:8099", "token": "keep"})
    c.put("/api/v1/edibl/config", json={"url": "http://e:8099", "token": ""})
    assert c.get("/api/v1/edibl/config").get_json()["tokenSet"] is True


def test_clear_token(noauth_app):
    c = noauth_app.test_client()
    c.put("/api/v1/edibl/config", json={"url": "http://e:8099", "token": "wrong"})
    c.put("/api/v1/edibl/config", json={"clearToken": True})
    assert c.get("/api/v1/edibl/config").get_json()["tokenSet"] is False


def test_url_must_be_http(noauth_app):
    r = noauth_app.test_client().put("/api/v1/edibl/config", json={"url": "ftp://e"})
    assert r.status_code == 422


def test_config_endpoints_require_auth(client):
    assert client.get("/api/v1/edibl/config").status_code == 401
    assert client.put("/api/v1/edibl/config", json={}).status_code == 401
    assert client.get("/api/v1/edibl/discover").status_code == 401


def test_client_uses_ui_config_over_env(noauth_app):
    """The EdiblClient built in a request must pick up the DB override."""
    from app.services.edibl import EdiblClient
    app = noauth_app
    with app.app_context():
        app.config["SETTINGS"].values["EDIBL_URL"] = "http://env:8099"
    c = app.test_client()
    c.put("/api/v1/edibl/config", json={"url": "http://ui:8099"})
    # Within a request the client should resolve to the UI value.
    with app.test_request_context("/api/v1/edibl/status"):
        from app.auth import load_current_user
        from flask import g
        g.current_user = load_current_user()
        g.current_group = g.current_user.group
        assert EdiblClient.from_settings().base_url == "http://ui:8099"


def test_discover_returns_none_cleanly_without_supervisor(noauth_app, monkeypatch):
    from app.services.ai import discovery
    monkeypatch.setattr(discovery, "_supervisor_addons", lambda: [])
    monkeypatch.setattr(discovery.httpx, "get",
                        lambda *a, **k: (_ for _ in ()).throw(discovery.httpx.ConnectError("x")))
    body = noauth_app.test_client().get("/api/v1/edibl/discover").get_json()
    assert body["found"] is False and "hint" in body


def test_test_connection_does_not_persist_url(noauth_app):
    """GET /edibl/status?url=... probes without saving (regression: Test used to
    persist the typed URL)."""
    c = noauth_app.test_client()
    c.put("/api/v1/edibl/config", json={"url": "http://saved:8099"})
    c.get("/api/v1/edibl/status?url=http://typed-but-not-saved:8099")
    assert c.get("/api/v1/edibl/config").get_json()["url"] == "http://saved:8099"


def test_edibl_config_isolated_between_groups(app):
    from app.services.edibl_config import set_config, effective
    with app.app_context():
        from app.models import Group
        from app.extensions import db
        g1 = Group(name="A")
        g2 = Group(name="B")
        db.session.add_all([g1, g2])
        db.session.commit()
        set_config(g1.id, url="http://g1:8099", token="tok1")
        set_config(g2.id, url="http://g2:8099")
        base = app.config["SETTINGS"]
        assert effective(base, g1.id)["url"] == "http://g1:8099"
        assert effective(base, g2.id)["url"] == "http://g2:8099"
        assert effective(base, g1.id)["token"] == "tok1"
        assert effective(base, g2.id)["token"] == ""   # g2 never set one; no bleed


def test_discover_edibl_via_supervisor(monkeypatch):
    """Auto-discovery on a Supervised install: query /addons, resolve the
    hash-prefixed edibl slug to its real hostname:ingress_port, probe status."""
    import httpx
    from app.services.ai import discovery as d

    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    responses = {
        "http://supervisor/addons": {"data": {"addons": [
            {"slug": "a0d7b954_edibl", "name": "Edibl"},
            {"slug": "core_mosquitto", "name": "Mosquitto"}]}},
        "http://supervisor/addons/a0d7b954_edibl/info":
            {"data": {"hostname": "a0d7b954-edibl", "ingress_port": 7746}},
    }

    class Resp:
        def __init__(self, status=200, data=None):
            self.status_code, self._d = status, data
        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("x", request=None, response=None)
        def json(self):
            return self._d

    def fake_get(url, headers=None, timeout=None):
        if url in responses:
            return Resp(data=responses[url])
        if url == "http://a0d7b954-edibl:7746/api/v1/status":
            return Resp(status=200)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(d.httpx, "get", fake_get)
    found = d.discover_edibl()
    assert found == {"url": "http://a0d7b954-edibl:7746", "via": "supervisor",
                     "needsAuth": False}


def test_discover_edibl_flags_needs_auth(monkeypatch):
    """A reachable Edibl that answers 401 is found, flagged needsAuth."""
    import httpx
    from app.services.ai import discovery as d

    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")

    class Resp:
        def __init__(self, status=200, data=None):
            self.status_code, self._d = status, data
        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("x", request=None, response=None)
        def json(self):
            return self._d

    def fake_get(url, headers=None, timeout=None):
        if url == "http://supervisor/addons":
            return Resp(data={"data": {"addons": [{"slug": "edibl", "name": "Edibl"}]}})
        if url == "http://supervisor/addons/edibl/info":
            return Resp(data={"data": {"hostname": "edibl", "ingress_port": 7746}})
        if url == "http://edibl:7746/api/v1/status":
            return Resp(status=401)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(d.httpx, "get", fake_get)
    assert d.discover_edibl()["needsAuth"] is True
