"""AI endpoints: recipe import and provider status.

More AI features (meal planning, pantry suggestions, the cooking agent) attach
to this blueprint in later milestones.
"""
import json
from datetime import date, timedelta

import httpx
from flask import Blueprint, request, jsonify

from ..extensions import db
from ..models import Recipe, PantryItem, MealPlanEntry
from ..auth import login_required, current_group
from ..schemas.serializers import recipe_out, mealplan_entry_out
from ..utils import unique_slug
from ..services.ai.base import ProviderError
from ..services.ai.registry import get_provider, list_providers
from ..services.ai.recipe_import import import_recipe, UnsafeURLError
from ..services.pantry import rank_recipes
from .recipes import _apply

bp = Blueprint("ai", __name__)


@bp.get("/ai/providers")
@login_required
def providers():
    return jsonify({"providers": list_providers()})


@bp.post("/ai/import")
@login_required
def import_recipe_endpoint():
    """Import a recipe from a URL or pasted text and save it to the group.

    URL imports try structured (JSON-LD) extraction first and only use the AI
    provider as a fallback, so a well-marked-up page imports even with no
    provider configured.
    """
    data = request.get_json(force=True) or {}
    # Coerce defensively — a non-string url/text must not 500.
    url = str(data.get("url") or "").strip()
    text = str(data.get("text") or "").strip()
    if not url and not text:
        return jsonify({"error": "provide a url or text to import"}), 422

    # A provider is only needed for the AI fallback; resolve leniently so a
    # JSON-LD URL import still works without one.
    provider = None
    try:
        provider = get_provider()
    except ProviderError:
        provider = None

    try:
        payload = import_recipe(url=url, text=text, provider=provider)
    except UnsafeURLError as exc:
        # Must be checked before ValueError — UnsafeURLError subclasses it.
        return jsonify({"error": str(exc)}), 400
    except ValueError:
        # Reached the AI path with no provider configured.
        return jsonify({"error": "no AI provider configured for this import"}), 503
    except ProviderError as exc:
        return jsonify({"error": str(exc)}), 502
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError) as exc:
        return jsonify({"error": f"could not fetch the URL: {exc}"}), 502

    name = payload.get("name") or "Imported Recipe"
    recipe = Recipe(
        name=name,
        slug=unique_slug(Recipe, current_group().id, name),
        group_id=current_group().id,
    )
    db.session.add(recipe)
    _apply(recipe, {k: v for k, v in payload.items() if k != "name"})
    db.session.commit()
    return jsonify(recipe_out(recipe)), 201


@bp.post("/ai/suggest")
@login_required
def suggest():
    """Pantry-aware 'what can I cook now?' — deterministic, no provider needed.

    Ranks the group's recipes by how well the current pantry covers their
    ingredients. Optional ``limit`` caps the result count.
    """
    gid = current_group().id
    data = request.get_json(silent=True) or {}
    try:
        limit = min(int(data.get("limit", 20) or 20), 100)
    except (ValueError, TypeError):
        limit = 20
    recipes = db.session.query(Recipe).filter_by(group_id=gid).all()
    pantry = db.session.query(PantryItem).filter_by(group_id=gid).all()
    return jsonify({"suggestions": rank_recipes(recipes, pantry)[:limit]})


@bp.post("/ai/plan")
@login_required
def plan_week():
    """Generate a meal plan with the AI provider and save it as plan entries.

    Body: ``{start, days, mealTypes, servings, preferences}``. The model is
    given the group's saved recipes so it can reference them by id; meals it
    invents come through as free-text titles.
    """
    gid = current_group().id
    data = request.get_json(force=True) or {}
    try:
        provider = get_provider()
    except ProviderError as exc:
        return jsonify({"error": str(exc)}), 503

    start = _parse_date(data.get("start")) or date.today()
    try:
        days = max(1, min(int(data.get("days", 7) or 7), 14))
    except (ValueError, TypeError):
        days = 7
    meal_types = data.get("mealTypes") or ["dinner"]
    servings = int(data.get("servings") or 0)
    preferences = (data.get("preferences") or "").strip()

    recipes = db.session.query(Recipe).filter_by(group_id=gid).all()
    catalog = [
        {"id": r.id, "name": r.name, "tags": [t.name for t in r.tags]}
        for r in recipes
    ]

    system = (
        "You are a household meal planner. Prefer recipes from the provided "
        "catalog (reference them by their exact id). You may invent a meal as "
        "a plain title when nothing fits. Respect the stated preferences and "
        "dietary constraints. Do not repeat the same recipe more than twice."
    )
    prompt = (
        f"Plan {days} day(s) of meals for these meal types: "
        f"{', '.join(meal_types)}.\n"
        f"Servings target: {servings or 'unspecified'}.\n"
        f"Preferences/constraints: {preferences or 'none'}.\n\n"
        f"Available recipes (JSON): {json.dumps(catalog)}\n\n"
        'Return JSON: {"days":[{"offset":0,"meals":[{"mealType":"dinner",'
        '"recipeId":"<id or empty>","title":"<title if no recipe>"}]}]}. '
        "offset is the 0-based day number from the start date."
    )
    try:
        result = provider.complete_json(prompt, system=system, max_tokens=4096)
    except ProviderError as exc:
        return jsonify({"error": str(exc)}), 502

    valid_ids = {r.id for r in recipes}
    created = []
    for day in result.get("days", []):
        try:
            offset = int(day.get("offset", 0))
        except (ValueError, TypeError):
            offset = 0
        entry_date = start + timedelta(days=offset)
        for meal in day.get("meals", []):
            rid = meal.get("recipeId") or None
            if rid not in valid_ids:
                rid = None
            entry = MealPlanEntry(
                date=entry_date,
                meal_type=meal.get("mealType", "dinner"),
                title=meal.get("title", ""),
                servings=servings,
                recipe_id=rid,
                group_id=gid,
            )
            db.session.add(entry)
            created.append(entry)
    db.session.commit()
    return jsonify({"entries": [mealplan_entry_out(e) for e in created]}), 201


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
