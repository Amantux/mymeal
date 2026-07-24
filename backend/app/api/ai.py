"""AI endpoints: recipe import and provider status.

More AI features (meal planning, pantry suggestions, the cooking agent) attach
to this blueprint in later milestones.
"""
import json
import re
from datetime import date, timedelta

import httpx
from flask import Blueprint, request, jsonify

from ..extensions import db
from ..models import Recipe, MealPlanEntry
from ..auth import login_required, owner_required, current_group
from ..schemas.serializers import recipe_out, mealplan_entry_out
from ..utils import unique_slug
from ..services.ai.base import ProviderError
from ..services.ai.registry import get_provider, list_providers
from ..services.ai.recipe_import import import_recipe, UnsafeURLError
from .recipes import _apply, download_image_to_recipe

bp = Blueprint("ai", __name__)


@bp.get("/ai/discover-ollama")
@owner_required
def discover_ollama_endpoint():
    """Find a local Ollama server so the user does not type a URL.

    Home Assistant's Ollama integration connects OUT to an Ollama server and
    does not expose it to other apps — so myMeal cannot route through Home
    Assistant. It can, however, reach the very same server directly, which is
    what this finds. Bounded and best-effort: it never raises and never blocks
    for long.
    """
    from ..services.ai.discovery import discover_ollama

    found = discover_ollama()
    if not found:
        return jsonify({
            "found": False,
            "hint": "No Ollama server answered on the usual addresses. Start "
                    "Ollama and make sure it listens on the network "
                    "(OLLAMA_HOST=0.0.0.0), then set MYMEAL_OLLAMA_HOST.",
        })
    return jsonify({"found": True, **found})


@bp.get("/ai/providers")
@login_required
def providers():
    return jsonify({"providers": list_providers()})


def _base_settings():
    from flask import current_app
    return current_app.config["SETTINGS"]


@bp.get("/ai/settings")
@owner_required
def get_ai_settings():
    """Editable AI-provider config for the UI. Never returns the API key —
    only whether one is set."""
    from ..services.ai.provider_config import settings_view

    return jsonify(settings_view(_base_settings(), current_group().id))


@bp.put("/ai/settings")
@owner_required
def put_ai_settings():
    """Persist the AI provider config (overrides the env / add-on default, and
    is remembered across restarts).

    Body: {provider?, baseUrl?, model?, apiKey?}. A field left out is unchanged;
    an empty string CLEARS that override (falls back to the env default). A
    blank/omitted apiKey leaves the stored key untouched.
    """
    from ..services.ai.provider_config import (
        VALID_PROVIDERS, set_overrides, settings_view,
    )

    data = request.get_json(silent=True) or {}
    provider = data.get("provider")
    if provider is not None and str(provider) not in VALID_PROVIDERS:
        return jsonify({"error": f"unknown provider {provider!r}"}), 422

    # A non-empty base URL must be http(s). It is operator-set config, but the
    # DB path bypasses the env-layer URL parser, so validate here to avoid
    # storing a scheme (file:, gopher:, javascript:) that a later server-side
    # httpx call would act on.
    base_url = data.get("baseUrl")
    if base_url and not re.match(r"^https?://", str(base_url).strip()):
        return jsonify({"error": "baseUrl must start with http:// or https://"}), 422

    kwargs = {}
    if "provider" in data:
        kwargs["provider"] = str(data["provider"] or "")
    if "baseUrl" in data:
        kwargs["base_url"] = str(data["baseUrl"] or "")
    if "model" in data:
        kwargs["model"] = str(data["model"] or "")
    # Only touch the key when a non-empty value is sent, so re-saving the form
    # (which never receives the stored key back) does not wipe it. An explicit
    # clearApiKey removes it (the only way to delete a wrong key).
    if data.get("clearApiKey"):
        kwargs["clear_api_key"] = True
    elif data.get("apiKey"):
        kwargs["api_key"] = str(data["apiKey"])

    set_overrides(current_group().id, **kwargs)
    return jsonify(settings_view(_base_settings(), current_group().id))


@bp.post("/ai/models")
@owner_required
def list_ai_models():
    """List models available on the provider, for the UI picker. Probes the
    values in the request body (provider/baseUrl/apiKey) WITHOUT persisting, so
    'List models' does not save the form as a side effect. Best-effort — returns
    [] rather than erroring."""
    from ..services.ai.provider_config import list_models, probe_config

    data = request.get_json(silent=True) or {}
    eff = probe_config(_base_settings(), current_group().id,
                       provider=data.get("provider"), base_url=data.get("baseUrl"),
                       api_key=data.get("apiKey"))
    return jsonify({"provider": eff.AI_PROVIDER, "models": list_models(eff)})


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
    # Download the source image after the recipe has an id (filename = <id>.ext).
    # Best-effort: never fail the import over a missing/oversized image.
    if payload.get("imageUrl"):
        download_image_to_recipe(recipe, payload["imageUrl"])
        db.session.commit()
    return jsonify(recipe_out(recipe)), 201


@bp.post("/ai/suggest")
@login_required
def suggest():
    """Inventory-aware 'what can I cook now?' — deterministic, no provider needed.

    Ranks the group's recipes by how well the on-hand inventory (from the
    companion Edibl app) covers their ingredients. Optional ``limit`` caps the
    result count.

    Inventory is owned by Edibl, not myMeal. With no Edibl configured this
    reports the feature as unavailable rather than ranking against nothing.
    """
    from ..services.edibl import EdiblClient
    from ..services.inventory import rank_recipes

    gid = current_group().id
    data = request.get_json(silent=True) or {}
    try:
        limit = min(int(data.get("limit", 20) or 20), 100)
    except (ValueError, TypeError):
        limit = 20

    inv = EdiblClient.from_settings().on_hand()
    if not inv["available"]:
        # 200 with a flag, not an error: this is a missing-optional-dependency,
        # and the UI should prompt to connect Edibl, not show a failure.
        return jsonify({"suggestions": [], "inventorySource": "edibl",
                        "ediblAvailable": False, "message": inv["reason"]})

    recipes = db.session.query(Recipe).filter_by(group_id=gid).all()
    return jsonify({"suggestions": rank_recipes(recipes, inv["items"])[:limit],
                    "inventorySource": "edibl", "ediblAvailable": True})


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
    # Coerce user input defensively — mealTypes may arrive as a string, a
    # non-list, or a list of non-strings; preferences/servings may be junk.
    raw_types = data.get("mealTypes")
    if isinstance(raw_types, str):
        raw_types = [raw_types]
    if not isinstance(raw_types, list) or not raw_types:
        raw_types = ["dinner"]
    meal_types = [str(t) for t in raw_types]
    try:
        servings = int(data.get("servings") or 0)
    except (ValueError, TypeError):
        servings = 0
    preferences = str(data.get("preferences") or "").strip()

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
    days_out = result.get("days")
    if not isinstance(days_out, list):
        days_out = []
    for day in days_out:
        if not isinstance(day, dict):
            continue  # model returned an off-shape entry — skip it
        try:
            offset = int(day.get("offset", 0))
        except (ValueError, TypeError):
            offset = 0
        entry_date = start + timedelta(days=offset)
        meals = day.get("meals")
        if not isinstance(meals, list):
            continue
        for meal in meals:
            if not isinstance(meal, dict):
                continue
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
