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
from ...models import Recipe, PantryItem, MealPlanEntry, ShoppingList, ShoppingListItem
from ..pantry import rank_recipes
from .base import AIProvider

SYSTEM = (
    "You are myMeal, a friendly and practical home cooking assistant. Help the "
    "user find recipes, decide what to cook, plan meals, and manage their "
    "shopping list. Use the provided tools to look things up in the user's own "
    "collection before answering — don't invent recipes they don't have unless "
    "they ask you to suggest something new. Keep answers concise and useful."
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
        "name": "list_pantry",
        "description": "List what the user currently has in their pantry.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "what_can_i_cook",
        "description": "Rank the user's recipes by how well their current pantry "
        "covers the ingredients.",
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

    if name == "list_pantry":
        items = db.session.query(PantryItem).filter_by(group_id=gid).all()
        return [
            {"item": p.label, "quantity": p.quantity, "unit": p.unit} for p in items
        ]

    if name == "what_can_i_cook":
        recipes = db.session.query(Recipe).filter_by(group_id=gid).all()
        pantry = db.session.query(PantryItem).filter_by(group_id=gid).all()
        return rank_recipes(recipes, pantry)[:5]

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
        db.session.add(
            ShoppingListItem(display=item_text, position=pos, shopping_list_id=sl.id)
        )
        # Flush, don't commit — the request handler owns the single commit so a
        # later failure in the turn rolls this back atomically.
        db.session.flush()
        return {"added": item_text, "list": sl.name}

    return {"error": f"unknown tool {name}"}


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

    for _ in range(max_iters):
        result = provider.chat(messages, system=SYSTEM, tools=TOOLS)
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
    final = provider.chat(messages, system=SYSTEM)
    return {"reply": final.content or "", "trace": trace}
