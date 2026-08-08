"""myMeal MCP server — exposes cooking tools to Home Assistant.

Runs in the same container as the app (a lightweight second process) and calls
the local myMeal REST API. Home Assistant's **MCP Client** integration connects
to the SSE endpoint and can then answer things like "what's for dinner?", "what
can I make right now?", and manage the shopping list by voice via Assist.

Run:  python mcp_server.py    (serves SSE on MYMEAL_MCP_HOST:MYMEAL_MCP_PORT/sse)
"""
import datetime
import hmac
import json
import os
import sys

import httpx
from mcp.server.fastmcp import FastMCP

# Config comes through the settings registry (parses /data/options.json + env
# once), NOT raw os.environ — the entrypoint exports only RESOLVED_MCP_* and
# never the token or the debug-tools flag, so reading raw env made the
# mcp_debug_tools add-on option a silent no-op. Mirrors the HomeHoard/Edibl
# sidecars (one config adapter, per the SOP).
from app.settings import load_settings as _load_settings_mod
_MCP_SETTINGS = _load_settings_mod()

API = os.environ.get("MYMEAL_MCP_API") or _MCP_SETTINGS.mcp_api
TOKEN = _MCP_SETTINGS.MCP_API_TOKEN or None  # only needed if app auth is enabled
_HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
# ⚠️ SINGLE-HOUSEHOLD BY DESIGN. Every tool call is made with this ONE token, so
# the sidecar always operates on that token's group regardless of which caller's
# key authenticated at the guard. The ASGI guard authenticates and applies
# read/write per the caller's key, but the data operated on is fixed. This is
# correct for the Home Assistant deployment (one household, auth disabled behind
# ingress) — the only supported MCP mode. A multi-tenant standalone instance
# with external MCP would let one household's key act on this group's data; that
# mode is explicitly unsupported and documented as such (mymeal/DOCS.md,
# docs/mcp-tenancy.md). Do NOT expose MCP externally on a shared instance.
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


def _put(path: str, json: dict | None = None):
    r = _HTTP.put(path, json=json or {})
    r.raise_for_status()
    return r.json()


def _delete(path: str) -> bool:
    r = _HTTP.delete(path)
    r.raise_for_status()
    return True


def _api_error(exc: httpx.HTTPStatusError) -> str:
    """The API's own error message, or a short status line — never a raw body dump."""
    try:
        message = str((exc.response.json() or {}).get("error") or "").strip()
    except ValueError:
        message = ""
    return message or f"HTTP {exc.response.status_code}"


def _resolve(name_or_id: str):
    """``(recipe, candidates)`` — resolve a name/id, never guessing.

    Delegates to ``/recipes/resolve``, which ranks matches by name, tags and
    description and returns a confidence. A confident hit comes back as
    ``(recipe, [])``; anything ambiguous comes back as ``(None, candidates)`` so
    the caller asks which was meant instead of acting on a coin-flip. Both this
    server and the Home Assistant integration use that one endpoint, so the
    policy can't drift between them.
    """
    try:
        data = _get("/recipes/resolve", {"q": name_or_id})
    except httpx.HTTPStatusError:
        return None, []
    if data.get("confidence") == "high":
        return data.get("recipe"), []
    return None, data.get("candidates") or []


def _describe_candidate(c: dict) -> str:
    """One candidate as a line a user can actually choose between."""
    bits = [f"{c.get('name')} (id {c.get('id')})"]
    tags = [t for t in (c.get("tags") or []) if t]
    if tags:
        bits.append(f"tags: {', '.join(tags[:4])}")
    if c.get("description"):
        bits.append(str(c["description"]))
    if c.get("matchedOn") and c["matchedOn"] != "name":
        bits.append(f"matched on {c['matchedOn']}")
    return " — ".join(bits)


def _clarify(name_or_id: str, candidates: list[dict]) -> str:
    """Ask which recipe was meant. Returned INSTEAD of acting, so nothing is lost."""
    listed = "; ".join(_describe_candidate(c) for c in candidates)
    return (f"'{name_or_id}' matches several recipes: {listed}. "
            "Nothing was changed — show these to the user, ask which one they "
            "mean, then call again with that recipe's id.")


def _clarify_dict(name_or_id: str, candidates: list[dict]) -> dict:
    """The same clarification for tools that return structured data."""
    return {
        "needsClarification": True,
        "question": f"Which recipe did you mean by '{name_or_id}'?",
        "candidates": candidates,
        "hint": "Call again with the id of the intended recipe.",
    }


def _version_count(recipe_id: str) -> int | None:
    """How many saved versions a delete would take with it; None if unreadable."""
    try:
        return len(_get(f"/recipes/{recipe_id}/versions").get("items", []))
    except httpx.HTTPStatusError:
        return None


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
    """Get a recipe's ingredients and steps (by name or id).

    If the name is ambiguous this returns `needsClarification` with the matching
    candidates (name, tags, description) instead of a recipe — show them to the
    user and ask which they meant, then call again with that id.
    """
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify_dict(name_or_id, candidates)
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
        # _api_error, never exc.response.text: a raw upstream body can carry a
        # DSN or key material, and every other tool here already curates.
        return {"error": f"Planning failed: {_api_error(exc)}"}
    return {"planned": len(data.get("entries", []))}


@mcp.tool()
def start_cooking(name_or_id: str) -> str:
    """Start reading a recipe's steps aloud. Say 'next step' to continue."""
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify(name_or_id, candidates)
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
    # An ambiguous name is harmless here: the session key falls back to what was
    # said, and an unknown key just means "not cooking that yet".
    recipe, _candidates = _resolve(name_or_id)
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
def update_recipe(name_or_id: str, name: str | None = None,
                  ingredients: list | None = None, steps: list | None = None,
                  servings: int | None = None) -> str:
    """Edit an existing recipe in place. Only the fields you pass are changed.

    WARNING: `ingredients` and `steps` REPLACE the whole list when supplied — omit
    them unless you are passing the complete new list. To change a single line,
    call get_recipe first and send the full edited list back. The previous state
    is snapshotted automatically, so restore_recipe_version can undo this.

    An ambiguous name edits nothing and returns the candidates instead — ask
    which recipe was meant rather than picking one.
    """
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify(name_or_id, candidates)
    if not recipe:
        return f"No recipe matching '{name_or_id}'."
    body: dict = {}
    if name:
        body["name"] = name
    if ingredients is not None:
        body["ingredients"] = [{"display": str(x)} for x in ingredients]
    if steps is not None:
        body["steps"] = [{"text": str(x)} for x in steps]
    if servings is not None:
        body["servings"] = servings
    if not body:
        return ("Nothing to update — pass a new name, ingredients, steps, "
                "or servings.")
    try:
        r = _put(f"/recipes/{recipe['id']}", body)
    except httpx.HTTPStatusError as exc:
        return f"Could not update {recipe['name']}: {_api_error(exc)}"
    changed = ", ".join(sorted(body))
    return f"Updated '{r.get('name', recipe['name'])}' ({changed})."


@mcp.tool()
def delete_recipe(name_or_id: str, confirm: bool = False) -> str:
    """Permanently delete a recipe — its ingredients, steps and EVERY saved
    version and experiment go with it. There is no undo.

    This takes two calls, and that is not optional:

    1. Call it WITHOUT `confirm`. Nothing is deleted; you get a preview of
       exactly what would be lost.
    2. Show that preview to the user in your reply and wait for their answer.
    3. Only if they agree, call again with confirm=true.

    Never pass confirm=true on the first call, and never decide to confirm on
    the user's behalf — a wrong delete cannot be recovered.

    If the name matches several recipes this returns the candidates and deletes
    nothing; ask which one and pass its id.
    """
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify(name_or_id, candidates)
    if not recipe:
        return f"No recipe matching '{name_or_id}'."
    if not confirm:
        n_versions = _version_count(recipe["id"])
        saved = ("an unknown number of saved versions" if n_versions is None
                 else f"{n_versions} saved version(s)/experiment(s)")
        return (f"About to permanently delete '{recipe['name']}' — "
                f"{len(recipe.get('ingredients', []))} ingredient(s), "
                f"{len(recipe.get('steps', []))} step(s) and {saved}. "
                "This cannot be undone. Show this to the user; if they agree, "
                "call delete_recipe again with confirm=true.")
    try:
        _delete(f"/recipes/{recipe['id']}")
    except httpx.HTTPStatusError as exc:
        return f"Could not delete {recipe['name']}: {_api_error(exc)}"
    return f"Deleted recipe '{recipe['name']}'."


# --- versions & experiments ------------------------------------------------- #
# Every edit auto-snapshots the previous state (browsable history). An
# "experiment" is a deliberate branch you can tweak and cook without touching the
# live recipe, then promote or discard.
def _version_or_error(recipe_id: str, version_id: str):
    """(version_dict, None) or (None, message). Includes the stored snapshot."""
    try:
        return _get(f"/recipes/{recipe_id}/versions/{version_id}"), None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None, f"No version '{version_id}' on that recipe."
        return None, f"Could not read that version: {_api_error(exc)}"


@mcp.tool()
def list_recipe_versions(name_or_id: str) -> dict:
    """List a recipe's saved versions — automatic edit history plus experiments.

    Use the returned `id` with update_experiment, add_experiment_feedback,
    promote_experiment, restore_recipe_version or discard_experiment.
    """
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify_dict(name_or_id, candidates)
    if not recipe:
        return {"error": f"No recipe matching '{name_or_id}'."}
    items = _get(f"/recipes/{recipe['id']}/versions").get("items", [])
    return {
        "recipe": recipe["name"],
        "versions": [
            {
                "id": v["id"],
                "kind": v.get("kind"),          # "auto" (history) or "experiment"
                "status": v.get("status"),      # open | promoted | discarded
                "label": v.get("label"),
                "rating": v.get("rating"),
            }
            for v in items
        ],
    }


@mcp.tool()
def start_recipe_experiment(name_or_id: str, label: str, note: str = "") -> str:
    """Branch an experiment from a recipe: an editable copy of it as it is now.

    Tweak it with update_experiment, cook it, record how it went with
    add_experiment_feedback, then promote_experiment to adopt it. The live recipe
    is untouched until you promote.

    An ambiguous recipe name returns the candidates instead of branching — ask
    which one was meant.
    """
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify(name_or_id, candidates)
    if not recipe:
        return f"No recipe matching '{name_or_id}'."
    v = _post(f"/recipes/{recipe['id']}/versions",
              {"label": label, "note": note})
    return (f"Started experiment '{v.get('label') or label}' on "
            f"{recipe['name']} (version id {v['id']}).")


@mcp.tool()
def update_experiment(name_or_id: str, version_id: str, name: str | None = None,
                      ingredients: list | None = None, steps: list | None = None,
                      servings: int | None = None) -> str:
    """Edit an experiment's contents. The LIVE recipe is not changed.

    Only the fields you pass are altered — the rest of the experiment's snapshot
    is preserved. As with update_recipe, passing `ingredients` or `steps`
    replaces that whole list. Only open experiments are editable.
    """
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify(name_or_id, candidates)
    if not recipe:
        return f"No recipe matching '{name_or_id}'."
    version, err = _version_or_error(recipe["id"], version_id)
    if err:
        return err
    # Merge into the EXISTING snapshot: the endpoint stores what it is sent, so
    # sending only the changed keys would drop everything else.
    snap = dict(version.get("snapshot") or {})
    if name:
        snap["name"] = name
    if ingredients is not None:
        snap["ingredients"] = [{"display": str(x)} for x in ingredients]
    if steps is not None:
        snap["steps"] = [{"text": str(x)} for x in steps]
    if servings is not None:
        snap["servings"] = servings
    try:
        _put(f"/recipes/{recipe['id']}/versions/{version_id}", snap)
    except httpx.HTTPStatusError as exc:
        return f"Could not edit that experiment: {_api_error(exc)}"
    return (f"Updated experiment '{version.get('label') or version_id}' "
            f"— {recipe['name']} itself is unchanged.")


@mcp.tool()
def add_experiment_feedback(name_or_id: str, version_id: str,
                            rating: int | None = None, feedback: str = "") -> str:
    """Record how a version turned out: a 0-5 rating and/or free-text notes."""
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify(name_or_id, candidates)
    if not recipe:
        return f"No recipe matching '{name_or_id}'."
    body: dict = {}
    if rating is not None:
        body["rating"] = rating
    if feedback:
        body["feedback"] = feedback
    if not body:
        return "Tell me a rating (0-5) or some feedback to record."
    try:
        _post(f"/recipes/{recipe['id']}/versions/{version_id}/feedback", body)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return f"No version '{version_id}' on {recipe['name']}."
        return f"Could not record that feedback: {_api_error(exc)}"
    return f"Recorded feedback on that version of {recipe['name']}."


@mcp.tool()
def promote_experiment(name_or_id: str, version_id: str) -> str:
    """Adopt an experiment as the live recipe.

    Reversible: the recipe's current state is snapshotted first, so
    restore_recipe_version can put it back.
    """
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify(name_or_id, candidates)
    if not recipe:
        return f"No recipe matching '{name_or_id}'."
    try:
        r = _post(f"/recipes/{recipe['id']}/versions/{version_id}/promote")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return f"No version '{version_id}' on {recipe['name']}."
        return f"Could not promote that version: {_api_error(exc)}"
    return (f"Promoted that version — '{r.get('name', recipe['name'])}' is now "
            "the live recipe. The previous version is still in its history.")


@mcp.tool()
def restore_recipe_version(name_or_id: str, version_id: str) -> str:
    """Roll a recipe back to any saved version (edit history or experiment).

    Reversible: the current state is snapshotted before the restore.
    """
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify(name_or_id, candidates)
    if not recipe:
        return f"No recipe matching '{name_or_id}'."
    try:
        r = _post(f"/recipes/{recipe['id']}/versions/{version_id}/restore")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return f"No version '{version_id}' on {recipe['name']}."
        return f"Could not restore that version: {_api_error(exc)}"
    return f"Restored '{r.get('name', recipe['name'])}' to that version."


@mcp.tool()
def discard_experiment(name_or_id: str, version_id: str,
                       confirm: bool = False) -> str:
    """Permanently delete one saved version or experiment, with its notes and
    rating. The live recipe is unaffected, but the version cannot be recovered.

    Two calls, same as delete_recipe: call it WITHOUT `confirm` to get a preview,
    show that to the user, and only call again with confirm=true once they
    agree. Never confirm on your own initiative.
    """
    recipe, candidates = _resolve(name_or_id)
    if candidates:
        return _clarify(name_or_id, candidates)
    if not recipe:
        return f"No recipe matching '{name_or_id}'."
    version, err = _version_or_error(recipe["id"], version_id)
    if err:
        return err
    label = version.get("label") or version.get("kind") or version_id
    if not confirm:
        return (f"About to permanently delete the '{label}' version of "
                f"{recipe['name']}, along with its notes and rating. "
                f"{recipe['name']} itself is unaffected, but this version cannot "
                "be recovered. Show this to the user; if they agree, call "
                "discard_experiment again with confirm=true.")
    try:
        _delete(f"/recipes/{recipe['id']}/versions/{version_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return f"No version '{version_id}' on {recipe['name']}."
        return f"Could not discard that version: {_api_error(exc)}"
    return f"Discarded the '{label}' version of {recipe['name']}."


@mcp.tool()
def plan_meal(name_or_id: str, day: str = "", meal_type: str = "dinner") -> str:
    """Add a recipe to the meal plan for a day (YYYY-MM-DD, defaults to today).

    An unknown name is planned as a free-text meal; an AMBIGUOUS one plans
    nothing and returns the candidates, so ask which recipe was meant.
    """
    when = day or datetime.date.today().isoformat()
    recipe, candidates = _resolve(name_or_id)
    # Ambiguous ≠ unknown: planning the wrong saved recipe is worse than asking,
    # so clarify rather than silently falling through to a free-text meal.
    if candidates:
        return _clarify(name_or_id, candidates)
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
    "list_recipe_versions",
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


def _key_scope(raw: str):
    """Scope of a live ApiToken ('full'|'rest'|'mcp'|'debug'), or None.

    Separate from _access_for because the debug tools gate on scope, not on the
    read/write class. Fail-closed (None) on any DB error.
    """
    if not raw:
        return None
    try:
        app = _get_app()
        from app.extensions import db
        from app.models import ApiToken
        from app.models.api_token import hash_token
        with app.app_context():
            rec = db.session.query(ApiToken).filter_by(token_hash=hash_token(raw)).first()
            scope = (rec.scope or "full") if rec is not None else None
            db.session.remove()
            return scope
    except Exception as exc:  # noqa: BLE001 — fail closed
        print(f"mymeal-mcp: scope check failed: {exc}", file=sys.stderr)
        return None


def _debug_key_exists() -> bool:
    """True if a 'debug'-scoped key exists. Raises on a DB error (fail closed)."""
    app = _get_app()
    from app.extensions import db
    from app.models import ApiToken
    with app.app_context():
        exists = (db.session.query(ApiToken.id)
                  .filter(ApiToken.scope == "debug").first() is not None)
        db.session.remove()
        return exists


def _is_message_path(scope) -> bool:
    """Whether this request targets the JSON-RPC message endpoint.

    Deliberately NOT also checking the HTTP method. FastMCP mounts the endpoint
    with Starlette's Mount, which matches on path alone, and
    SseServerTransport.handle_post_message never checks the method either — so
    PUT/GET/DELETE are processed exactly like POST. Gating on method == "POST"
    meant every rule below saw an empty tool list and applied nothing.
    """
    return "/messages" in scope.get("path", "")


def _tool_names(scope, body: bytes) -> list:
    """Tool names in a JSON-RPC body, or [] if this is not a message request."""
    if not _is_message_path(scope):
        return []
    try:
        parsed = json.loads(body or b"{}")
    except Exception:  # noqa: BLE001
        return []
    items = parsed if isinstance(parsed, list) else [parsed]
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("method") != "tools/call":
            continue
        params = it.get("params")
        # params may legitimately be a list; .get on it raised, turning an
        # unauthenticated request into a 500.
        name = params.get("name") if isinstance(params, dict) else None
        out.append(name if isinstance(name, str) else "")
    return out


def _audit(names, raw: str, outcome: str) -> None:
    """One line per tool call: which tool, which key, allowed or denied.

    There was no record at all of what ran over MCP — the surface most likely to
    need an audit trail. Arguments are deliberately NOT logged.
    """
    try:
        from app.services.metrics import record
        # NOT `key=`: the log redactor treats `key=<value>` as a credential.
        hint = f"{raw[:7]}~" if raw else "none"
        for name in names or ["-"]:
            record("mcp_tool", name=name or "-", client=hint, outcome=outcome)
    except Exception as exc:  # noqa: BLE001 - auditing must never break a request
        print(f"mymeal-mcp: audit logging failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Debug tools. Registered only when MCP_DEBUG_TOOLS is on, and callable only by
# a `debug`-scoped key — see _guard. They read this instance's own logs and
# metrics so an AI client can investigate a problem without an operator having
# to copy log text around by hand.
# ---------------------------------------------------------------------------
DEBUG_TOOLS = frozenset({
    "debug_recent_logs", "debug_error_summary", "debug_metrics",
    "debug_diagnostics",
})


def _register_debug_tools() -> None:
    from app.services import debug_logs, metrics
    from app.settings import load_settings

    # myMeal's MCP process has no module-level settings object (unlike its
    # siblings), so resolve once here at registration time.
    _settings = load_settings()
    data_dir = _settings.data_dir

    @mcp.tool()
    def debug_recent_logs(level: str = "INFO", contains: str = "",
                          request_id: str = "", since: str = "",
                          limit: int = 100) -> dict:
        """Recent log lines from this myMeal instance, newest last.

        level: minimum severity (DEBUG|INFO|WARNING|ERROR). contains: plain
        substring filter, not a regex. request_id: show one request's lines,
        using the id from an error response or the X-Request-Id header. since:
        an ISO-8601 UTC timestamp; only later lines are returned. Credentials
        are redacted; timestamps are UTC.
        """
        return debug_logs.read_recent(data_dir, level=level, contains=contains,
                                      request_id=request_id, since=since, limit=limit)

    @mcp.tool()
    def debug_error_summary(limit: int = 20) -> dict:
        """Recent warnings and errors grouped by message shape, most frequent
        first — "what is going wrong repeatedly", rather than a raw line dump.
        Each group carries the last request id, to look that request up."""
        return debug_logs.error_summary(data_dir, limit=limit)

    @mcp.tool()
    def debug_metrics(kind: str = "") -> dict:
        """Recent timing samples (background jobs, MCP tool calls) from THIS
        process. The MCP server and each web worker keep separate rings, so
        treat these as a sample; the log lines (kind=metric) are the complete
        record and are visible via debug_recent_logs."""
        kinds = [kind] if kind else ["job", "mcp_tool"]
        return {"summaries": [metrics.summary(k) for k in kinds],
                "recent": metrics.recent(kind or None, limit=50)}

    @mcp.tool()
    def debug_diagnostics() -> dict:
        """Coarse runtime facts: database backend, configured providers, which
        settings are non-default and where each came from. Secrets are
        redacted — this is the same information the config check prints."""
        settings = _settings
        uri = settings.sqlalchemy_uri
        redacted = settings.redacted()
        return {
            "app": "myMeal",
            "dbBackend": "sqlite" if uri.startswith("sqlite") else "postgresql",
            "aiProvider": settings.AI_PROVIDER or "none",
            "authDisabled": bool(settings.DISABLE_AUTH),
            "nonDefault": {name: redacted.get(name)
                           for name, src in sorted(settings.sources.items())
                           if src != "default"},
            "sources": {k: v for k, v in sorted(settings.sources.items()) if v != "default"},
        }


def _expose_external() -> bool:
    """Whether the MCP port is deliberately reachable from outside Home Assistant."""
    return _bool_env("MYMEAL_MCP_EXPOSE_EXTERNAL")


def _guard(asgi_app, server_token: str):
    """The single ASGI gate, ALWAYS installed.

    Previously a wrapper was chosen once at boot and, with external exposure off
    and no static token, none was installed at all — so there was nothing to
    enforce anything with. It is now always present; what it *requires* depends
    on the tool:

    * **debug tools** — always require a `debug`-scoped key, on every network.
      Logs contain sign-in emails and tracebacks that can carry a database
      password, a different sensitivity class from recipe data.
    * **domain tools** — unchanged: open on the Home Assistant network, and when
      exposed externally every request needs a valid key, with read-only keys
      held to the READ_TOOLS allowlist.
    """
    external = _expose_external()

    async def wrapper(scope, receive, send):
        if scope["type"] != "http":
            return await asgi_app(scope, receive, send)

        # latin-1 decode never raises on arbitrary bytes (unlike utf-8), so a
        # malformed Authorization header 401s cleanly instead of 500ing.
        header = dict(scope.get("headers") or []).get(
            b"authorization", b"").decode("latin-1")
        raw = header[len("Bearer "):].strip() if header.startswith("Bearer ") else ""

        body = b""
        if _is_message_path(scope):
            messages, body = await _read_body(receive)
            receive = _replay(messages, receive)
        names = _tool_names(scope, body)
        wants_debug = any(n in DEBUG_TOOLS for n in names)
        wants_domain = any(n and n not in DEBUG_TOOLS for n in names)

        async def deny(status, message):
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": message})

        if wants_debug:
            if _key_scope(raw) != "debug":
                _audit(names, raw, "denied")
                return await deny(
                    403, b"the debug tools require an API key with the 'debug' scope")
            if wants_domain:
                # A batch mixing both would otherwise let a debug key reach a
                # domain tool by pairing it with a debug one.
                _audit(names, raw, "denied")
                return await deny(403, b"a debug key may not call the domain tools")
        else:
            if names and _key_scope(raw) == "debug":
                _audit(names, raw, "denied")
                return await deny(403, b"a debug key may only call the debug tools")
            # AUTHENTICATION depends on how the port is exposed: external needs
            # a valid key on every request; on the internal network a server
            # token is required only if one is configured, and is otherwise
            # open (the HA zero-setup path).
            if external:
                if not _authorized(header, server_token):
                    return await deny(401, b"unauthorized")
            elif server_token and not _authorized(header, server_token):
                return await deny(401, b"unauthorized")

            # AUTHORIZATION depends only on the KEY, never on the exposure. A
            # read-only key must not reach a write tool on ANY path — this check
            # used to live inside `if external:`, so mapping the MCP port to the
            # LAN and handing out a read-only key granted full write. _access_for
            # returns "write" for the server token and for no-key (the open
            # path), so only a genuinely read-scoped key is blocked here.
            if (_access_for(header, server_token) == "read"
                    and _is_tools_call_body(body)):
                _audit(names, raw, "denied")
                return await deny(403, b"this API key is read-only")

        if names:
            _audit(names, raw, "allowed")
        await asgi_app(scope, receive, send)

    return wrapper


if __name__ == "__main__":
    # Configure logging explicitly here: this process builds its Flask app
    # LAZILY (only when a key lookup needs the DB), so waiting for create_app
    # meant the sidecar produced no log file at all.
    from app.logging_setup import configure as _configure_logging
    from app.settings import load_settings as _load_settings
    _configure_logging(_load_settings(), process="mcp")

    host = os.environ.get("MYMEAL_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MYMEAL_MCP_PORT", "7851"))
    server_token = os.environ.get("MYMEAL_MCP_SERVER_TOKEN", "")
    expose_external = _bool_env("MYMEAL_MCP_EXPOSE_EXTERNAL")

    if _MCP_SETTINGS.MCP_DEBUG_TOOLS:
        # Fail-closed, mirroring the external-exposure check: serving log-reading
        # tools that nobody can authenticate to would be worse than not serving
        # them.
        try:
            has_debug_key = _debug_key_exists()
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: mymeal-mcp: could not verify a debug key exists: {exc}. "
                  "Not registering the debug tools.", file=sys.stderr)
            has_debug_key = False
        if has_debug_key:
            _register_debug_tools()
            print("mymeal-mcp: debug tools ON — they require an API key with the "
                  "'debug' scope, on every network.", file=sys.stderr)
        else:
            print("WARNING: mcp_debug_tools is on but no 'debug'-scoped API key "
                  "exists. Mint one in Settings -> Access & keys (scope 'Debug'), "
                  "then restart. Not serving the debug tools.", file=sys.stderr)

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
                  "exists. Mint one in Settings -> Access & keys (scope MCP), then "
                  "restart. Refusing to serve an unauthenticated public endpoint.",
                  file=sys.stderr)
            sys.exit(1)
        print("mymeal-mcp: external exposure ON — every request must present a "
              "Full/MCP API key.", file=sys.stderr)
    elif not server_token:
        print("NOTE: the MCP endpoint is open to the Home Assistant network so "
              "Assist works without setup. Publishing the port outside HA needs "
              "mcp_expose_external and a minted MCP key.", file=sys.stderr)

    # ALWAYS wrapped. What the guard requires depends on the tool: debug tools
    # always need a debug key; domain tools keep the open-internally behaviour.
    app = _guard(mcp.sse_app(), server_token)

    import uvicorn
    uvicorn.run(app, host=host, port=port)
