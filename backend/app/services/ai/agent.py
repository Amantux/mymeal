"""The conversational cooking assistant.

Runs a provider-agnostic tool-calling loop: the model decides which tool to
call, we execute it against the group's data, feed the result back as text, and
repeat until the model answers. Feeding results back as plain text (rather than
each vendor's native tool-result block) keeps the loop identical across Claude,
OpenAI, and Ollama — the same tool schema and executor drive all three, and the
MCP server (later milestone) reuses these executors.
"""
from __future__ import annotations

import json
from datetime import date

from ...extensions import db
from ...models import Recipe, MealPlanEntry, ShoppingList, ShoppingListItem
from ..edibl import EdiblClient
from ..inventory import rank_recipes
from .base import AIProvider

SYSTEM = (
    "You are myMeal, a friendly and practical home cooking assistant. Help the "
    "user find recipes, decide what to cook, plan meals, and manage their "
    "shopping list. Use the provided tools to look things up in the user's own "
    "collection before answering — don't invent recipes they don't have unless "
    "they ask you to suggest something new. Keep answers concise and useful."
)

_EDIBL_PROMPT = (
    " Edibl (the food-inventory app) is connected, so you can also check what's on "
    "hand, add or use up pantry stock, and add to Edibl's shopping list using the "
    "edibl_* tools."
)

TOOLS = [
    {
        "name": "search_recipes",
        "description": "Search the user's saved recipes by name or keyword.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_recipe",
        "description": "Get full details (ingredients and steps) for one recipe "
        "by name or id.",
        "parameters": {
            "type": "object",
            "properties": {"name_or_id": {"type": "string"}},
            "required": ["name_or_id"],
        },
    },
    {
        "name": "list_inventory",
        "description": "List what food the user currently has on hand (from Edibl).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "what_can_i_cook",
        "description": "Rank the user's recipes by how well their on-hand "
        "inventory (from Edibl) covers the ingredients.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "whats_for_dinner",
        "description": "Get the planned meals for a date (YYYY-MM-DD; defaults to "
        "today).",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string"}},
        },
    },
    {
        "name": "add_to_shopping_list",
        "description": "Add an item to the user's shopping list.",
        "parameters": {
            "type": "object",
            "properties": {"item": {"type": "string"}},
            "required": ["item"],
        },
    },
]


def _find_recipe(gid, name_or_id):
    r = db.session.get(Recipe, name_or_id)
    if r and r.group_id == gid:
        return r
    r = db.session.query(Recipe).filter_by(group_id=gid, slug=name_or_id).first()
    if r:
        return r
    like = f"%{name_or_id}%"
    return (
        db.session.query(Recipe)
        .filter(Recipe.group_id == gid, Recipe.name.ilike(like))
        .first()
    )


def execute_tool(gid: str, name: str, args: dict):
    """Run one tool against the group's data. Returns a JSON-serializable value."""
    # The model may emit a non-dict (array/scalar) as arguments — never trust it.
    if not isinstance(args, dict):
        args = {}
    if name.startswith("edibl_"):
        return _edibl_tool(name, args)
    if name == "search_recipes":
        like = f"%{(args.get('query') or '').strip()}%"
        rows = (
            db.session.query(Recipe)
            .filter(Recipe.group_id == gid, Recipe.name.ilike(like))
            .order_by(Recipe.name.asc())
            .limit(15)
            .all()
        )
        return [{"id": r.id, "name": r.name} for r in rows]

    if name == "get_recipe":
        r = _find_recipe(gid, str(args.get("name_or_id", "")))
        if not r:
            return {"error": "no matching recipe"}
        return {
            "id": r.id,
            "name": r.name,
            "servings": r.servings,
            "totalMinutes": r.total_minutes,
            "ingredients": [i.display for i in r.ingredients],
            "steps": [s.text for s in r.steps],
        }

    if name == "list_inventory":
        inv = EdiblClient.from_settings().on_hand()
        if not inv["available"]:
            return {"available": False, "message": inv["reason"], "items": []}
        return {"available": True, "items": inv["items"]}

    if name == "what_can_i_cook":
        inv = EdiblClient.from_settings().on_hand()
        if not inv["available"]:
            return {"available": False, "message": inv["reason"], "suggestions": []}
        recipes = db.session.query(Recipe).filter_by(group_id=gid).all()
        return {"available": True,
                "suggestions": rank_recipes(recipes, inv["items"])[:5]}

    if name == "whats_for_dinner":
        try:
            when = date.fromisoformat(str(args.get("date"))[:10])
        except (ValueError, TypeError):
            when = date.today()
        entries = (
            db.session.query(MealPlanEntry)
            .filter_by(group_id=gid, date=when)
            .all()
        )
        return {
            "date": when.isoformat(),
            "meals": [
                {
                    "mealType": e.meal_type,
                    "name": e.recipe.name if e.recipe else e.title,
                }
                for e in entries
            ],
        }

    if name == "add_to_shopping_list":
        item_text = str(args.get("item", "")).strip()
        if not item_text:
            return {"error": "no item given"}
        sl = (
            db.session.query(ShoppingList)
            .filter_by(group_id=gid)
            .order_by(ShoppingList.created_at.asc())
            .first()
        )
        if not sl:
            sl = ShoppingList(name="Shopping List", group_id=gid)
            db.session.add(sl)
            db.session.flush()
        pos = (max((i.position for i in sl.items), default=-1)) + 1
        item = ShoppingListItem(display=item_text, position=pos, shopping_list_id=sl.id)
        db.session.add(item)
        # Flush, don't commit — the request handler owns the single commit so a
        # later failure in the turn rolls this back atomically. The flush
        # populates item.id, which the action chip needs so the user can undo.
        db.session.flush()
        return {"added": item_text, "list": sl.name, "itemId": item.id}

    return {"error": f"unknown tool {name}"}


# Maps a mutating tool's result to a user-facing "action" chip — what the
# assistant actually DID, surfaced under its reply (Edibl-style), rather than a
# raw tool-name trace. Read-only tools produce no action. Returning None skips.
_ACTION_FORMATTERS = {
    "add_to_shopping_list": lambda r: (
        {"label": f'Added "{r["added"]}" to {r.get("list", "your list")}',
         "kind": "shopping", "icon": "🛒",
         # Structured undo the frontend maps to a known, safe call — NOT a raw
         # method+path from the server. Present only when we have the item id.
         **({"undo": {"kind": "shopping_item", "id": r["itemId"]}}
            if r.get("itemId") else {})}
        if r.get("added") else None
    ),
    # Cross-app (Edibl) mutations — all undoable via the server undo-proxy
    # (POST /ai/chat/undo -> Edibl), because the browser can't reach Edibl. The
    # undo descriptor is present only when we have the ids needed to reverse.
    "edibl_add_stock": lambda r: (
        {"label": f'Added {r.get("quantity", 1)} {r.get("unit", "")} '
                  f'{r["added"]} to the Edibl pantry'.replace("  ", " "),
         "kind": "stock", "icon": "🥫",
         **({"undo": {"kind": "edibl_stock", "id": r["lotId"]}}
            if r.get("lotId") else {})}
        if r.get("added") else None
    ),
    "edibl_record_consumption": lambda r: (
        {"label": f'Recorded {r["consumed"]} {r.get("outcome", "eaten")} in Edibl',
         "kind": "consume", "icon": "🍽️",
         **({"undo": {"kind": "edibl_unconsume", "lotId": r["lotId"],
                      "consumptionId": r["consumptionId"], "amount": r.get("amount")}}
            if r.get("lotId") and r.get("consumptionId") else {})}
        if r.get("consumed") else None
    ),
    "edibl_add_to_shopping": lambda r: (
        {"label": f'Added {r["addedToShopping"]} to Edibl\'s shopping list',
         "kind": "shopping", "icon": "🛒",
         **({"undo": {"kind": "edibl_shopping", "id": r["shoppingId"]}}
            if r.get("shoppingId") else {})}
        if r.get("addedToShopping") else None
    ),
}


def actions_from_trace(trace: list[dict]) -> list[dict]:
    """Derive action chips from the tool trace (mutations only)."""
    actions = []
    for step in trace:
        fmt = _ACTION_FORMATTERS.get(step.get("tool"))
        if not fmt:
            continue
        action = fmt(step.get("result") or {})
        if action:
            actions.append(action)
    return actions


# --------------------------------------------------------------------------- #
# Edibl bridge — pantry management, ONLY advertised when Edibl is connected.
# A standalone myMeal never sees these tools, so there is no dependency on the
# two apps being deployed together. Each call is bounded and degrades to an
# {available: False} message when Edibl is unreachable. Cross-app mutations are
# surfaced as action chips but are NOT undoable here (myMeal's undo runs in the
# browser, which can't reach Edibl — undo those from Edibl's own chat).
# --------------------------------------------------------------------------- #
_EDIBL_TOOLS = [
    {"name": "edibl_do_i_have",
     "description": "Check whether an ingredient is on hand in Edibl (how much, where).",
     "parameters": {"type": "object", "properties": {
         "ingredient": {"type": "string"}}, "required": ["ingredient"]}},
    {"name": "edibl_whats_in_stock",
     "description": "List what food is on hand in Edibl (optionally filtered by name).",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}},
    {"name": "edibl_expiring_soon",
     "description": "List Edibl items expiring within N days.",
     "parameters": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "edibl_add_stock",
     "description": "Add food to the Edibl pantry (e.g. after shopping). Expiry auto-estimated.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string"}, "quantity": {"type": "number"},
         "unit": {"type": "string"}, "category": {"type": "string"},
         "storage_method": {"type": "string"}, "location": {"type": "string"},
         "freshness": {"type": "string"}}, "required": ["name"]}},
    {"name": "edibl_record_consumption",
     "description": "Record that Edibl food was eaten, spoiled, expired, or discarded.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string"}, "quantity": {"type": "number"},
         "outcome": {"type": "string"}}, "required": ["name"]}},
    {"name": "edibl_add_to_shopping",
     "description": "Add an item to Edibl's shopping list.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string"}, "quantity": {"type": "number"},
         "unit": {"type": "string"}}, "required": ["name"]}},
]


def _edibl_connected() -> bool:
    try:
        return EdiblClient.from_settings().configured
    except Exception:  # noqa: BLE001 — no config/context
        return False


def _sibling_tools() -> list[dict]:
    """Edibl tools, only when Edibl is connected — standalone myMeal shows none."""
    return _EDIBL_TOOLS if _edibl_connected() else []


def _edibl_reason(res: dict) -> str:
    if res.get("reachable"):
        return f"Edibl responded with an error ({res.get('error')})."
    return f"Edibl is unreachable ({res.get('error')})."


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _edibl_tool(name: str, args: dict):
    """Execute one Edibl management tool via the sibling client."""
    client = EdiblClient.from_settings()
    if not client.configured:
        return {"available": False, "message": "Edibl (inventory) isn't connected."}

    if name == "edibl_do_i_have":
        res = client.have(str(args.get("ingredient", "")))
        return res.get("data") if res.get("ok") else {
            "available": False, "message": _edibl_reason(res)}

    if name == "edibl_whats_in_stock":
        stock = client.get_stock()
        if not stock.get("ok"):
            return {"available": False, "message": _edibl_reason(stock)}
        items = stock["items"]
        q = str(args.get("query", "")).strip().lower()
        if q:
            items = [i for i in items if q in (i.get("name") or "").lower()]
        return {"items": items[:60]}

    if name == "edibl_expiring_soon":
        res = client.expiring(_to_int(args.get("days"), 5))
        if not res.get("ok"):
            return {"available": False, "message": _edibl_reason(res)}
        data = res.get("data") or {}
        # Edibl may return a bare list or {"items": [...]} — handle both.
        items = data if isinstance(data, list) else data.get("items", [])
        return {"items": items}

    if name == "edibl_add_stock":
        res = client.add_stock(
            str(args.get("name", "")).strip(),
            quantity=args.get("quantity") or 1, unit=args.get("unit") or "count",
            category=args.get("category") or "other",
            storage_method=args.get("storage_method") or "refrigerated",
            location=args.get("location") or "", freshness=args.get("freshness") or "")
        if not res.get("ok"):
            return {"error": _edibl_reason(res)}
        lot = res.get("data") or {}
        return {"added": args.get("name"), "quantity": args.get("quantity") or 1,
                "unit": args.get("unit") or "count", "lotId": lot.get("id")}

    if name == "edibl_record_consumption":
        lot = client.find_lot(str(args.get("name", "")))
        if not lot:
            return {"error": f"No Edibl stock matching '{args.get('name')}'."}
        res = client.consume(lot["id"], quantity=args.get("quantity") or 1,
                             outcome=args.get("outcome") or "eaten")
        if not res.get("ok"):
            return {"error": _edibl_reason(res)}
        data = res.get("data") or {}
        return {"consumed": (lot.get("product") or {}).get("name") or args.get("name"),
                "quantity": args.get("quantity") or 1,
                "outcome": args.get("outcome") or "eaten",
                "lotId": lot["id"], "consumptionId": data.get("consumptionId"),
                "amount": data.get("consumedAmount")}

    if name == "edibl_add_to_shopping":
        res = client.add_shopping(str(args.get("name", "")).strip(),
                                  quantity=args.get("quantity") or 1,
                                  unit=args.get("unit") or "count")
        if not res.get("ok"):
            return {"error": _edibl_reason(res)}
        item = res.get("data") or {}
        return {"addedToShopping": args.get("name"), "shoppingId": item.get("id")}

    return {"error": f"unknown edibl tool {name}"}


def run_chat(
    gid: str,
    provider: AIProvider,
    history: list[dict],
    user_message: str,
    max_iters: int = 6,
) -> dict:
    """Drive one assistant turn (with tool use) and return the reply + trace.

    ``history`` is prior {role, content} messages. Returns
    ``{"reply": str, "trace": [ {tool, args, result}... ]}``.
    """
    messages = list(history) + [{"role": "user", "content": user_message}]
    trace: list[dict] = []
    # Add Edibl tools only when Edibl is connected, so standalone myMeal is
    # unchanged and never depends on Edibl being deployed alongside it.
    connected = _edibl_connected()
    tools = TOOLS + _EDIBL_TOOLS if connected else TOOLS
    system = SYSTEM + _EDIBL_PROMPT if connected else SYSTEM

    for _ in range(max_iters):
        result = provider.chat(messages, system=system, tools=tools)
        if not result.tool_calls:
            return {"reply": result.content or "", "trace": trace}
        # Record the assistant's intent, then run each tool and feed results back.
        messages.append(
            {"role": "assistant", "content": result.content or "(using tools)"}
        )
        for call in result.tool_calls:
            try:
                output = execute_tool(gid, call.name, call.arguments)
            except Exception as exc:  # noqa: BLE001 - feed errors back, never 500
                output = {"error": f"{call.name} failed: {exc}"}
            trace.append(
                {"tool": call.name, "args": call.arguments, "result": output}
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Result of {call.name}({json.dumps(call.arguments)}): "
                        f"{json.dumps(output)}"
                    ),
                }
            )
    # Exhausted the loop — ask once more for a plain answer.
    final = provider.chat(messages, system=system)
    return {"reply": final.content or "", "trace": trace}
