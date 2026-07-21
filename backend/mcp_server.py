"""myMeal MCP server — exposes cooking tools to Home Assistant.

Runs in the same container as the app (a lightweight second process) and calls
the local myMeal REST API. Home Assistant's **MCP Client** integration connects
to the SSE endpoint and can then answer things like "what's for dinner?", "what
can I make right now?", and manage the shopping list by voice via Assist.

Run:  python mcp_server.py    (serves SSE on MYMEAL_MCP_HOST:MYMEAL_MCP_PORT/sse)
"""
import datetime
import os

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
    sl = _default_list()
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


def _require_token(asgi_app, token: str):
    """ASGI wrapper: reject HTTP requests without `Authorization: Bearer <token>`.
    Mirrors Edibl/HomeHoard so Home Assistant can authenticate to the MCP server
    (important now that it can create recipes and delete meal plans)."""
    import hmac
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
    import sys

    host = os.environ.get("MYMEAL_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MYMEAL_MCP_PORT", "7851"))
    server_token = os.environ.get("MYMEAL_MCP_SERVER_TOKEN", "")
    app = mcp.sse_app()
    if server_token:
        app = _require_token(app, server_token)
    else:
        print("WARNING: MYMEAL_MCP_SERVER_TOKEN unset — MCP endpoint is "
              "UNAUTHENTICATED (fine only on a trusted internal network).",
              file=sys.stderr)
    import uvicorn
    uvicorn.run(app, host=host, port=port)
