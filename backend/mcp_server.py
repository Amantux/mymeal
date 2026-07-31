"""myMeal MCP server — exposes cooking tools to Home Assistant.

Runs in the same container as the app (a lightweight second process) and calls
the local myMeal REST API. Home Assistant's **MCP Client** integration connects
to the SSE endpoint and can then answer things like "what's for dinner?", "what
can I make right now?", and manage the shopping list by voice via Assist.

Run:  python mcp_server.py    (serves SSE on MYMEAL_MCP_HOST:MYMEAL_MCP_PORT/sse)
"""
import datetime
import hmac
import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

API = os.environ.get("MYMEAL_MCP_API", "http://127.0.0.1:7850/api/v1")
TOKEN = os.environ.get("MYMEAL_MCP_API_TOKEN")  # only needed if app auth is enabled
_HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
_HTTP = httpx.Client(base_url=API, headers=_HEADERS, timeout=15)

# The MCP SDK ships DNS-rebinding protection that, by default, rejects any
# request whose Host header isn't localhost. Home Assistant's MCP Client
# connects to this add-on by its container hostname, so we must allow non-local
# hosts. This server is only reachable on the trusted Supervisor/LAN network.
_fastmcp_kwargs: dict = {}
try:  # mcp >= ~1.9.4
    from mcp.server.transport_security import TransportSecuritySettings

    _fastmcp_kwargs["transport_security"] = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
except ImportError:  # older SDK without the host check — nothing to relax
    pass

mcp = FastMCP("myMeal", **_fastmcp_kwargs)

# In-memory voice cooking sessions: recipe name -> {steps: [...], index: int}.
# Lives only in this process; fine for a single-household add-on.
_COOKING: dict[str, dict] = {}


def _get(path: str, params: dict | None = None):
    r = _HTTP.get(path, params=params)
    r.raise_for_status()
    return r.json()


def _post(path: str, json: dict | None = None):
    r = _HTTP.post(path, json=json or {})
    r.raise_for_status()
    return r.json()


def _delete(path: str) -> bool:
    r = _HTTP.delete(path)
    r.raise_for_status()
    return True


def _resolve_recipe(name_or_id: str):
    """Find one recipe by id/slug (direct) or name (first search hit)."""
    try:
        return _get(f"/recipes/{name_or_id}")
    except httpx.HTTPStatusError:
        pass
    results = _get("/search", {"q": name_or_id, "types": "recipe"}).get("results", [])
    if not results:
        return None
    return _get(f"/recipes/{results[0]['id']}")


def _default_list():
    """Return the first shopping list, creating one if none exists."""
    lists = _get("/shopping-lists").get("items", [])
    if lists:
        return lists[0]
    return _post("/shopping-lists", {"name": "Shopping List"})


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def search_recipes(query: str) -> list[dict]:
    """Search saved recipes by name or keyword. Returns matches with names."""
    results = _get("/search", {"q": query, "types": "recipe"}).get("results", [])
    return [{"name": r["name"], "id": r["id"]} for r in results]


@mcp.tool()
def get_recipe(name_or_id: str) -> dict:
    """Get a recipe's ingredients and steps (by name or id)."""
    recipe = _resolve_recipe(name_or_id)
    if not recipe:
        return {"error": f"No recipe matching '{name_or_id}'."}
    return {
        "name": recipe["name"],
        "servings": recipe.get("servings"),
        "totalMinutes": recipe.get("totalMinutes"),
        "ingredients": [i["display"] for i in recipe.get("ingredients", [])],
        "steps": [s["text"] for s in recipe.get("steps", [])],
    }


@mcp.tool()
def whats_for_dinner(day: str = "") -> dict:
    """What's planned to eat on a day (YYYY-MM-DD, defaults to today)."""
    when = day or datetime.date.today().isoformat()
    data = _get("/mealplans", {"start": when, "end": when})
    meals = [
        {
            "mealType": e["mealType"],
            "name": (e.get("recipe") or {}).get("name") or e.get("title"),
        }
        for e in data.get("items", [])
    ]
    if not meals:
        return {"date": when, "message": "Nothing is planned for that day."}
    return {"date": when, "meals": meals}


@mcp.tool()
def what_can_i_cook() -> list[dict]:
    """Suggest recipes you can make now, ranked by on-hand inventory (Edibl)."""
    data = _post("/ai/suggest", {"limit": 5})
    if data.get("ediblAvailable") is False:
        return [{"message": data.get("message", "Inventory is provided by Edibl, "
                 "which isn't connected.")}]
    out = []
    for s in data.get("suggestions", []):
        out.append(
            {
                "name": s["name"],
                "haveOnHand": f"{s['haveCount']}/{s['totalCount']}",
                "missing": s.get("missing", []),
            }
        )
    return out or [{"message": "No recipes to match — add some first."}]


@mcp.tool()
def get_shopping_list() -> dict:
    """Show the current shopping list (unchecked items)."""
    # Read-only: never auto-create a list (that would be a write). If none exists
    # yet, report an empty list rather than calling _default_list()'s create path.
    lists = _get("/shopping-lists").get("items", [])
    if not lists:
        return {"list": "Shopping List", "items": []}
    sl = lists[0]
    items = [i["display"] for i in sl.get("items", []) if not i.get("checked")]
    return {"list": sl["name"], "items": items}


@mcp.tool()
def add_to_shopping_list(item: str) -> str:
    """Add an item to the shopping list."""
    if not item.strip():
        return "Tell me what to add."
    sl = _default_list()
    _post(f"/shopping-lists/{sl['id']}/items", {"display": item})
    return f"Added {item} to {sl['name']}."


@mcp.tool()
def list_inventory() -> list[dict]:
    """List what food is currently on hand (from the Edibl inventory app)."""
    data = _get("/edibl/stock")
    if not data.get("configured"):
        return [{"message": "Inventory is provided by Edibl, which isn't connected."}]
    return [{"item": i["name"], "quantity": i.get("quantity"), "unit": i.get("unit")}
            for i in data.get("items", [])]


@mcp.tool()
def plan_week(preferences: str = "", days: int = 7) -> dict:
    """Generate a meal plan for the coming days using AI (needs a provider)."""
    try:
        data = _post("/ai/plan", {"days": days, "preferences": preferences})
    except httpx.HTTPStatusError as exc:
        return {"error": f"Planning failed: {exc.response.text}"}
    return {"planned": len(data.get("entries", []))}


@mcp.tool()
def start_cooking(name_or_id: str) -> str:
    """Start reading a recipe's steps aloud. Say 'next step' to continue."""
    recipe = _resolve_recipe(name_or_id)
    if not recipe:
        return f"No recipe matching '{name_or_id}'."
    steps = [s["text"] for s in recipe.get("steps", [])]
    if not steps:
        return f"{recipe['name']} has no steps recorded."
    _COOKING[recipe["name"].lower()] = {"steps": steps, "index": 0}
    return f"Let's cook {recipe['name']}. Step 1: {steps[0]}"


@mcp.tool()
def next_step(name_or_id: str) -> str:
    """Read the next step of a recipe you're cooking."""
    recipe = _resolve_recipe(name_or_id)
    key = recipe["name"].lower() if recipe else name_or_id.lower()
    session = _COOKING.get(key)
    if not session:
        return "You're not cooking that yet — say 'start cooking' first."
    session["index"] += 1
    steps = session["steps"]
    if session["index"] >= len(steps):
        _COOKING.pop(key, None)
        return "That was the last step. Enjoy your meal!"
    return f"Step {session['index'] + 1}: {steps[session['index']]}"


@mcp.tool()
def add_recipe(name: str, ingredients: list | None = None,
               steps: list | None = None, servings: int | None = None) -> str:
    """Create a new recipe. `ingredients` and `steps` are lists of plain strings."""
    body: dict = {"name": name}
    if ingredients:
        body["ingredients"] = [{"display": str(x)} for x in ingredients]
    if steps:
        body["steps"] = [{"text": str(x)} for x in steps]
    if servings is not None:
        body["servings"] = servings
    r = _post("/recipes", body)
    return f"Added recipe '{r.get('name', name)}'."


@mcp.tool()
def plan_meal(name_or_id: str, day: str = "", meal_type: str = "dinner") -> str:
    """Add a recipe to the meal plan for a day (YYYY-MM-DD, defaults to today)."""
    when = day or datetime.date.today().isoformat()
    recipe = _resolve_recipe(name_or_id)
    body = {"date": when, "mealType": meal_type}
    if recipe:
        body["recipeId"] = recipe["id"]
        label = recipe["name"]
    else:
        body["title"] = name_or_id  # free-text meal when there's no saved recipe
        label = name_or_id
    _post("/mealplans", body)
    return f"Planned {label} for {meal_type} on {when}."


@mcp.tool()
def remove_planned_meal(day: str = "", meal_type: str = "") -> str:
    """Remove planned meal(s) for a day (optionally only one meal type)."""
    when = day or datetime.date.today().isoformat()
    entries = _get("/mealplans", {"start": when, "end": when}).get("items", [])
    if meal_type:
        entries = [e for e in entries if e.get("mealType") == meal_type]
    if not entries:
        return f"Nothing planned to remove for {when}."
    removed = 0
    for e in entries:
        try:
            _delete(f"/mealplans/{e['id']}")
            removed += 1
        except httpx.HTTPError:  # tolerate a mid-loop failure, report the truth
            pass
    return f"Removed {removed} of {len(entries)} planned meal(s) for {when}."


@mcp.tool()
def remove_from_shopping_list(item: str) -> str:
    """Remove item(s) from the shopping list by (partial) name."""
    sl = _default_list()
    q = item.strip().lower()
    matches = [i for i in sl.get("items", []) if q in (i.get("display") or "").lower()]
    if not matches:
        return f"No shopping item matching '{item}'."
    removed = 0
    for i in matches:
        try:
            _delete(f"/shopping-lists/items/{i['id']}")
            removed += 1
        except httpx.HTTPError:  # tolerate a mid-loop failure, report the truth
            pass
    return f"Removed {removed} of {len(matches)} item(s) from {sl['name']}."


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


_app = None


def _get_app():
    """A minimal DB-only Flask app for API-key lookups, built once. We deliberately
    do NOT call the app's create_app() here — that would start a second background
    job worker in this sidecar. The main process/entrypoint already initialized the
    schema, so this only needs a session bound to the same database."""
    global _app
    if _app is None:
        from flask import Flask
        from app.extensions import db
        from app.settings import load_settings
        import app.models  # noqa: F401 — register tables on db.metadata

        s = load_settings()
        flask_app = Flask("mymeal-mcp-auth")
        flask_app.config["SQLALCHEMY_DATABASE_URI"] = s.sqlalchemy_uri
        flask_app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        # Match the REST app: pre-ping so a pooled connection dropped by an idle
        # timeout (shared Postgres) doesn't make a key lookup raise → spurious 401.
        flask_app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
        db.init_app(flask_app)
        _app = flask_app
    return _app


def _key_ok(raw: str) -> bool:
    """True if `raw` is a live ApiToken whose scope allows MCP (`mcp` or `full`)."""
    if not raw:
        return False
    try:
        app = _get_app()
        from app.extensions import db
        from app.models import ApiToken, hash_token
        with app.app_context():
            rec = (
                db.session.query(ApiToken)
                .filter_by(token_hash=hash_token(raw))
                .first()
            )
            ok = rec is not None and (rec.scope or "full") in ("mcp", "full")
            db.session.remove()
            return ok
    except Exception as exc:  # noqa: BLE001 — fail closed on any lookup error
        print(f"mymeal-mcp: key check failed: {exc}", file=sys.stderr)
        return False


def _mcp_capable_key_exists_when_ready(retries: int = 15, delay: float = 2.0) -> bool:
    """`_mcp_capable_key_exists`, tolerant of a not-yet-migrated DB at boot. The
    sidecar can start before the main app has run migrations, so a missing table or
    the `scope` column would otherwise make the startup check exit permanently (the
    supervise loop never restarts it). Retry on a DB structural error for a bounded
    window; a clean "no key" result (no exception) returns immediately. Re-raises
    after exhausting retries so the caller still fails closed."""
    import time

    from sqlalchemy.exc import OperationalError, ProgrammingError

    last = None
    for _ in range(retries):
        try:
            return _mcp_capable_key_exists()
        except (OperationalError, ProgrammingError) as exc:
            last = exc
            print(f"mymeal-mcp: DB not ready for the key check, retrying: {exc}",
                  file=sys.stderr)
            time.sleep(delay)
    raise last if last is not None else RuntimeError("DB schema never became ready")


def _mcp_capable_key_exists() -> bool:
    """True if at least one live key can authorize MCP (scope mcp or full). Used to
    refuse to serve when external exposure is on but no usable key exists yet."""
    app = _get_app()
    from app.extensions import db
    from app.models import ApiToken
    with app.app_context():
        exists = (
            db.session.query(ApiToken.id)
            .filter(ApiToken.scope.in_(("mcp", "full")))
            .first()
            is not None
        )
        db.session.remove()
        return exists


# Tools a read-only key MAY call. Anything not listed (mutating tools + any future
# tool) is denied to a read-only key — fail-safe, so a new tool is never silently
# writable. start_cooking/next_step only mutate an in-process narration dict.
READ_TOOLS = frozenset({
    "search_recipes", "get_recipe", "whats_for_dinner", "what_can_i_cook",
    "get_shopping_list", "list_inventory", "start_cooking", "next_step",
})


def _key_access(raw: str) -> str | None:
    """The access class ('write'|'read') of a live MCP-usable key, else None. Fails
    to None (caller treats as write, since _authorized already validated the key)."""
    if not raw:
        return None
    try:
        app = _get_app()
        from app.extensions import db
        from app.models import ApiToken, hash_token
        with app.app_context():
            rec = (
                db.session.query(ApiToken)
                .filter_by(token_hash=hash_token(raw))
                .first()
            )
            access = None
            if rec is not None and (rec.scope or "full") in ("mcp", "full"):
                access = rec.access or "write"
            db.session.remove()
            return access
    except Exception as exc:  # noqa: BLE001 — treat as write (already authorized)
        print(f"mymeal-mcp: access check failed: {exc}", file=sys.stderr)
        return None


def _access_for(header_value: str, server_token: str) -> str:
    """Access class of the authenticated caller. The static server token is full
    write; a Bearer key uses its stored access. Fail CLOSED: if a key's access can't
    be determined (DB error), treat it as `read` so a transient fault can't upgrade a
    read-only key to write."""
    if server_token and hmac.compare_digest(header_value, f"Bearer {server_token}"):
        return "write"
    if header_value.startswith("Bearer "):
        return _key_access(header_value[len("Bearer "):].strip()) or "read"
    return "write"


def _is_tools_call_body(raw_body: bytes):
    """Parse an MCP JSON-RPC POST body; return True if it contains a `tools/call`
    to a tool NOT in READ_TOOLS (i.e. a write a read-only key must be denied)."""
    import json as _json
    try:
        data = _json.loads(raw_body or b"{}")
    except Exception:  # noqa: BLE001 — unparseable → let the app 400 it, don't block
        return False

    def is_write(msg):
        return (isinstance(msg, dict) and msg.get("method") == "tools/call"
                and (msg.get("params") or {}).get("name") not in READ_TOOLS)

    if isinstance(data, list):   # JSON-RPC batch
        return any(is_write(m) for m in data)
    return is_write(data)


async def _read_body(receive):
    """Drain and return (buffered_messages, body_bytes) from an ASGI receive."""
    messages, body, more = [], b"", True
    while more:
        msg = await receive()
        messages.append(msg)
        if msg["type"] == "http.request":
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
        else:
            more = False
    return messages, body


def _replay(messages, receive):
    """A receive() that yields buffered messages first, then defers to the original."""
    pending = list(messages)

    async def _recv():
        if pending:
            return pending.pop(0)
        return await receive()

    return _recv


def _authorized(header_value: str, server_token: str) -> bool:
    """Authorized if the request presents the static server token (when configured)
    or a live REST key whose scope allows MCP."""
    if server_token and hmac.compare_digest(header_value, f"Bearer {server_token}"):
        return True
    if header_value.startswith("Bearer "):
        return _key_ok(header_value[len("Bearer "):].strip())
    return False


def _guard(asgi_app, server_token: str):
    """ASGI gate requiring auth on every HTTP request — used when the MCP server is
    exposed outside Home Assistant. Accepts the static server token or a scoped key."""
    async def wrapper(scope, receive, send):
        if scope["type"] == "http":
            # latin-1 decode never raises on arbitrary bytes (unlike utf-8), so a
            # malformed Authorization header 401s cleanly instead of 500ing.
            header = dict(scope.get("headers") or []).get(
                b"authorization", b"").decode("latin-1")
            if not _authorized(header, server_token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
            # Read-only keys: block mutating tool calls (the SSE message POST). The
            # tool runs in a separate task, so we screen the JSON-RPC body here.
            if (scope.get("method") == "POST"
                    and "/messages" in scope.get("path", "")
                    and _access_for(header, server_token) == "read"):
                messages, body = await _read_body(receive)
                if _is_tools_call_body(body):
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"text/plain")]})
                    await send({"type": "http.response.body",
                                "body": b"this API key is read-only"})
                    return
                receive = _replay(messages, receive)
        await asgi_app(scope, receive, send)

    return wrapper


def _require_token(asgi_app, token: str):
    """ASGI wrapper for the internal/HA-voice path: reject requests without the
    static `Authorization: Bearer <token>` (used when a token is set but the
    endpoint is NOT exposed externally)."""
    expected = f"Bearer {token}".encode()

    async def wrapper(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            # Compare on bytes: a non-ASCII Authorization header would make the
            # str form of compare_digest raise (500) instead of cleanly 401ing.
            if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await asgi_app(scope, receive, send)

    return wrapper


if __name__ == "__main__":
    host = os.environ.get("MYMEAL_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MYMEAL_MCP_PORT", "7851"))
    server_token = os.environ.get("MYMEAL_MCP_SERVER_TOKEN", "")
    expose_external = _bool_env("MYMEAL_MCP_EXPOSE_EXTERNAL")

    app = mcp.sse_app()
    if expose_external:
        # Reachable from outside HA → auth is MANDATORY. Refuse to serve at all
        # unless there is a way to authenticate (a scoped key or the static token),
        # so the endpoint is never open to the network.
        try:
            has_key = _mcp_capable_key_exists_when_ready()
        except Exception as exc:  # noqa: BLE001 — fail closed
            print(f"mymeal-mcp: could not check API keys ({exc}); refusing to serve "
                  "the externally-exposed MCP endpoint.", file=sys.stderr)
            sys.exit(1)
        if not has_key and not server_token:
            print("mymeal-mcp: mcp_expose_external is ON but no MCP/Full API key "
                  "exists. Mint one in Settings → Access & keys (scope MCP), then "
                  "restart. Refusing to serve an unauthenticated public endpoint.",
                  file=sys.stderr)
            sys.exit(1)
        app = _guard(app, server_token)
        print("mymeal-mcp: external exposure ON — every request must present a "
              "Full/MCP API key.", file=sys.stderr)
    elif server_token:
        app = _require_token(app, server_token)
    else:
        print("WARNING: MYMEAL_MCP_SERVER_TOKEN unset — MCP endpoint is "
              "UNAUTHENTICATED (fine only on a trusted internal network).",
              file=sys.stderr)
    import uvicorn
    uvicorn.run(app, host=host, port=port)
