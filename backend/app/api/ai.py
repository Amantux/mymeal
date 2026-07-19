"""AI endpoints: recipe import and provider status.

More AI features (meal planning, pantry suggestions, the cooking agent) attach
to this blueprint in later milestones.
"""
import httpx
from flask import Blueprint, request, jsonify

from ..extensions import db
from ..models import Recipe
from ..auth import login_required, current_group
from ..schemas.serializers import recipe_out
from ..utils import unique_slug
from ..services.ai.base import ProviderError
from ..services.ai.registry import get_provider, list_providers
from ..services.ai.recipe_import import import_recipe
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
    url = (data.get("url") or "").strip()
    text = (data.get("text") or "").strip()
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
    except ValueError:
        # Reached the AI path with no provider configured.
        return jsonify({"error": "no AI provider configured for this import"}), 503
    except ProviderError as exc:
        return jsonify({"error": str(exc)}), 502
    except httpx.HTTPError as exc:
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
