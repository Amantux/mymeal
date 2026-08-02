"""MCP external-exposure auth: per-client scoped API keys.

- Keys carry a scope (full | rest | mcp); default full.
- An `mcp`-scoped key is rejected at the REST API; a `full`/`rest` key is not.
- The MCP guard admits only keys whose scope allows MCP (mcp | full).
- Refuse-to-serve: `_mcp_capable_key_exists()` reflects whether exposure can start.
- The ASGI guard 401s unless a valid key or the static server token is presented.
"""
import asyncio

import pytest

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


# --- access class (read-write vs read-only) -----------------------------------

def _mint_access(client, access, scope="full", name="k"):
    return client.post("/api/v1/tokens", json={"name": name, "scope": scope, "access": access},
                       headers=_hdr("owner"), environ_overrides=SUP)


def test_create_defaults_to_write_access(noauth_app):
    assert _mint(_owner_client(noauth_app)).get_json()["access"] == "write"


def test_create_accepts_read_access(noauth_app):
    r = _mint_access(_owner_client(noauth_app), "read")
    assert r.status_code == 201 and r.get_json()["access"] == "read"


def test_create_rejects_unknown_access(noauth_app):
    assert _mint_access(_owner_client(noauth_app), "admin").status_code == 400


# --- REST: a read-only key can GET but not mutate -----------------------------

def test_read_key_blocked_from_mutating_rest(noauth_app):
    c = _owner_client(noauth_app)
    raw = _mint_access(c, "read").get_json()["token"]
    bearer = {"Authorization": f"Bearer {raw}"}
    assert c.get("/api/v1/tokens", headers=bearer).status_code == 200      # read ok
    assert c.post("/api/v1/tokens", json={"name": "x"}, headers=bearer).status_code == 403


def test_write_key_can_mutate_rest(noauth_app):
    c = _owner_client(noauth_app)
    raw = _mint_access(c, "write").get_json()["token"]
    bearer = {"Authorization": f"Bearer {raw}"}
    assert c.post("/api/v1/tokens", json={"name": "x"}, headers=bearer).status_code == 201


# --- MCP: a read-only key is limited to READ_TOOLS ----------------------------

def _run_guard_post(monkeypatch, body, access="read"):
    import json as _json
    monkeypatch.setattr(mcp_server, "_key_ok", lambda raw: True)
    monkeypatch.setattr(mcp_server, "_key_access", lambda raw: access)
    result = {"code": None, "passed": False, "got_body": None}

    async def inner(scope, receive, send):
        msg = await receive()                 # inner must still see the replayed body
        result["passed"] = True
        result["got_body"] = msg.get("body", b"")

    _prev = mcp_server._expose_external
    mcp_server._expose_external = lambda: True   # these cases are the exposed path
    try:
        guarded = mcp_server._guard(inner, server_token="")
    finally:
        mcp_server._expose_external = _prev
    pending = [{"type": "http.request", "body": _json.dumps(body).encode(), "more_body": False}]

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            result["code"] = msg["status"]

    scope = {"type": "http", "method": "POST", "path": "/messages/",
             "headers": [(b"authorization", b"Bearer x")]}
    asyncio.run(guarded(scope, receive, send))
    return result


def _call(name):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name}}


def test_read_key_blocked_from_write_tool(monkeypatch):
    r = _run_guard_post(monkeypatch, _call("add_recipe"), access="read")
    assert r["code"] == 403 and r["passed"] is False


def test_read_key_allows_read_tool_and_replays_body(monkeypatch):
    body = _call("search_recipes")
    r = _run_guard_post(monkeypatch, body, access="read")
    assert r["code"] is None and r["passed"] is True
    import json as _json
    assert _json.loads(r["got_body"]) == body        # body replayed intact to the app


def test_read_key_allows_non_tool_methods(monkeypatch):
    r = _run_guard_post(monkeypatch, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, access="read")
    assert r["passed"] is True and r["code"] is None


def test_read_key_denies_unknown_tool(monkeypatch):
    r = _run_guard_post(monkeypatch, _call("definitely_not_a_tool"), access="read")
    assert r["code"] == 403 and r["passed"] is False   # fail-safe: unlisted tool denied


def test_write_key_allows_write_tool(monkeypatch):
    r = _run_guard_post(monkeypatch, _call("add_recipe"), access="write")
    assert r["passed"] is True and r["code"] is None


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

    _prev = mcp_server._expose_external
    mcp_server._expose_external = lambda: True   # these cases are the exposed path
    try:
        guarded = mcp_server._guard(inner, server_token=server_token)
    finally:
        mcp_server._expose_external = _prev

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


def test_get_shopping_list_never_auto_creates(monkeypatch):
    # A read tool must not mutate: with no shopping list yet, get_shopping_list must
    # return an empty list WITHOUT POSTing to create one (which a read-only key would
    # otherwise be able to trigger).
    posted = []
    monkeypatch.setattr(mcp_server, "_get", lambda path: {"items": []})
    monkeypatch.setattr(mcp_server, "_post",
                        lambda *a, **k: posted.append(a) or {"id": 1, "name": "x", "items": []})
    fn = getattr(mcp_server.get_shopping_list, "fn", mcp_server.get_shopping_list)
    out = fn()
    assert out["items"] == []
    assert posted == []   # no create call


# ---- The debug scope --------------------------------------------------------
#
# A debug key reads this instance's own logs, which carry sign-in emails and
# tracebacks that can include a database password. It is a separate key class:
# denied at REST, denied on the domain tools, and the debug tools are denied to
# every other key on every network.

def _run_debug_guard(monkeypatch, tool, key_scope, external=False):
    import json as _json
    monkeypatch.setattr(mcp_server, "_key_scope", lambda raw: key_scope)
    monkeypatch.setattr(mcp_server, "_key_ok", lambda raw: True)
    monkeypatch.setattr(mcp_server, "_audit", lambda *a, **k: None)
    result = {"code": None, "passed": False}

    async def inner(scope, receive, send):
        result["passed"] = True

    _prev = mcp_server._expose_external
    mcp_server._expose_external = lambda: external
    try:
        guarded = mcp_server._guard(inner, server_token="")
    finally:
        mcp_server._expose_external = _prev

    body = _json.dumps(tool if isinstance(tool, list) else
                       {"method": "tools/call", "params": {"name": tool}}).encode()
    pending = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            result["code"] = msg["status"]

    headers = [(b"authorization", b"Bearer x")] if key_scope else []
    scope = {"type": "http", "method": "POST", "path": "/messages/", "headers": headers}
    asyncio.run(guarded(scope, receive, send))
    return result


def test_debug_key_may_call_a_debug_tool(monkeypatch):
    r = _run_debug_guard(monkeypatch, "debug_recent_logs", "debug")

    assert r["passed"] is True


def test_debug_key_may_not_call_a_domain_tool(monkeypatch):
    r = _run_debug_guard(monkeypatch, "search_recipes", "debug")

    assert r["code"] == 403 and r["passed"] is False


@pytest.mark.parametrize("scope", ["full", "mcp"])
def test_a_normal_key_may_not_call_a_debug_tool(monkeypatch, scope):
    """Least privilege in both directions — otherwise any Assist key could read
    the logs and 'debug only' would be a lie."""
    r = _run_debug_guard(monkeypatch, "debug_recent_logs", scope)

    assert r["code"] == 403 and r["passed"] is False


def test_an_unauthenticated_caller_may_not_call_a_debug_tool(monkeypatch):
    """The case that motivated always installing the guard: with external
    exposure off, myMeal previously installed NO guard at all."""
    r = _run_debug_guard(monkeypatch, "debug_recent_logs", None)

    assert r["code"] == 403 and r["passed"] is False


def test_domain_tools_stay_open_internally(monkeypatch):
    """Voice control must keep working without setup on the HA network."""
    r = _run_debug_guard(monkeypatch, "search_recipes", None)

    assert r["passed"] is True


def test_a_batch_cannot_smuggle_a_domain_tool_past_a_debug_key(monkeypatch):
    r = _run_debug_guard(monkeypatch, [
        {"method": "tools/call", "params": {"name": "debug_recent_logs"}},
        {"method": "tools/call", "params": {"name": "create_recipe"}},
    ], "debug")

    assert r["code"] == 403 and r["passed"] is False
