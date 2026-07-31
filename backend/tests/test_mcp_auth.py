"""MCP external-exposure auth: per-client scoped API keys.

- Keys carry a scope (full | rest | mcp); default full.
- An `mcp`-scoped key is rejected at the REST API; a `full`/`rest` key is not.
- The MCP guard admits only keys whose scope allows MCP (mcp | full).
- Refuse-to-serve: `_mcp_capable_key_exists()` reflects whether exposure can start.
- The ASGI guard 401s unless a valid key or the static server token is presented.
"""
import asyncio

import mcp_server
from app import auth

SUP = {"REMOTE_ADDR": "172.30.32.2"}


def _hdr(uid):
    return {"X-Remote-User-Id": uid}


def _mint(client, scope=None, name="k"):
    body = {"name": name}
    if scope is not None:
        body["scope"] = scope
    return client.post("/api/v1/tokens", json=body, headers=_hdr("owner"), environ_overrides=SUP)


def _owner_client(noauth_app):
    c = noauth_app.test_client()
    c.get("/api/v1/users/self", headers=_hdr("owner"), environ_overrides=SUP)
    return c


# --- token minting with a scope ------------------------------------------------

def test_create_defaults_to_full_scope(noauth_app):
    r = _mint(_owner_client(noauth_app))
    assert r.status_code == 201
    assert r.get_json()["scope"] == "full"


def test_create_accepts_mcp_scope(noauth_app):
    r = _mint(_owner_client(noauth_app), scope="mcp")
    assert r.status_code == 201
    assert r.get_json()["scope"] == "mcp"


def test_create_rejects_unknown_scope(noauth_app):
    assert _mint(_owner_client(noauth_app), scope="root").status_code == 400


# --- REST rejects an mcp-scoped key -------------------------------------------

def test_mcp_scope_key_rejected_at_rest_but_full_and_rest_pass(noauth_app):
    c = _owner_client(noauth_app)
    raw = {s: _mint(c, scope=s, name=s).get_json()["token"] for s in ("full", "rest", "mcp")}
    with noauth_app.app_context():
        assert auth._user_from_api_token(raw["mcp"]) is None      # blocked at REST
        assert auth._user_from_api_token(raw["full"]) is not None
        assert auth._user_from_api_token(raw["rest"]) is not None


# --- the MCP guard admits only mcp/full keys ----------------------------------

def test_key_ok_scope_gate(noauth_app, monkeypatch):
    c = _owner_client(noauth_app)
    raw = {s: _mint(c, scope=s, name=s).get_json()["token"] for s in ("full", "rest", "mcp")}
    monkeypatch.setattr(mcp_server, "_get_app", lambda: noauth_app)
    assert mcp_server._key_ok(raw["mcp"]) is True
    assert mcp_server._key_ok(raw["full"]) is True
    assert mcp_server._key_ok(raw["rest"]) is False       # rest key can't reach MCP
    assert mcp_server._key_ok("mm_not_a_real_key") is False
    assert mcp_server._key_ok("") is False


def test_mcp_capable_key_exists(noauth_app, monkeypatch):
    monkeypatch.setattr(mcp_server, "_get_app", lambda: noauth_app)
    c = _owner_client(noauth_app)
    # A DB with only a rest-scoped key cannot authorize MCP → refuse-to-serve.
    _mint(c, scope="rest", name="rest-only")
    assert mcp_server._mcp_capable_key_exists() is False
    _mint(c, scope="mcp", name="mcp-key")
    assert mcp_server._mcp_capable_key_exists() is True


# --- the ASGI guard 401s / passes based on the key check ----------------------

def _run_guard(server_token, header_bytes):
    """Build the guard around a sentinel inner app and drive one HTTP request."""
    result = {"code": None, "passed": False}

    async def inner(scope, receive, send):
        result["passed"] = True

    guarded = mcp_server._guard(inner, server_token=server_token)

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            result["code"] = msg["status"]

    scope = {"type": "http",
             "headers": [(b"authorization", header_bytes)] if header_bytes else []}
    asyncio.run(guarded(scope, receive, send))
    return result


def test_guard_rejects_without_valid_key(monkeypatch):
    monkeypatch.setattr(mcp_server, "_key_ok", lambda raw: raw == "good")
    assert _run_guard("", b"")["code"] == 401                 # no header
    assert _run_guard("", b"Bearer nope")["code"] == 401      # bad key
    ok = _run_guard("", b"Bearer good")                       # valid key passes
    assert ok["code"] is None and ok["passed"] is True


def test_guard_accepts_static_server_token(monkeypatch):
    monkeypatch.setattr(mcp_server, "_key_ok", lambda raw: False)
    assert _run_guard("s3cret", b"Bearer s3cret")["passed"] is True
    assert _run_guard("s3cret", b"Bearer wrong")["code"] == 401


def test_capable_key_check_retries_until_db_ready(monkeypatch):
    # Sidecar can boot before the main app migrates: the readiness wrapper retries a
    # DB structural error, then returns the real result once the schema is ready.
    from sqlalchemy.exc import OperationalError

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError("no such column: api_tokens.scope", None, None)
        return True

    monkeypatch.setattr(mcp_server, "_mcp_capable_key_exists", flaky)
    assert mcp_server._mcp_capable_key_exists_when_ready(retries=5, delay=0) is True
    assert calls["n"] == 3


def test_capable_key_check_reraises_after_retries(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def always_broken():
        raise OperationalError("table api_tokens does not exist", None, None)

    monkeypatch.setattr(mcp_server, "_mcp_capable_key_exists", always_broken)
    try:
        mcp_server._mcp_capable_key_exists_when_ready(retries=2, delay=0)
        raised = False
    except OperationalError:
        raised = True
    assert raised is True   # exhausted retries → re-raise so __main__ fails closed
