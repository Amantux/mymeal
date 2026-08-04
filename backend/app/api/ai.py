"""AI endpoints: recipe import and provider status.

More AI features (meal planning, pantry suggestions, the cooking agent) attach
to this blueprint in later milestones.
"""
import json
from datetime import date, timedelta

import httpx
from flask import Blueprint, request, jsonify, current_app, stream_with_context

from ..extensions import db
from ..services import conversions, ingredient_ai
from ..models import Recipe, MealPlanEntry
from ..auth import login_required, owner_required, current_group
from ..schemas.serializers import recipe_out, mealplan_entry_out
from ..utils import unique_slug
from ..services.ai.base import ProviderError
from ..services.ai.registry import get_provider, list_providers
from ..services.preferences import preferences_text
from ..services.ai.recipe_import import (
    import_recipe,
    generate_recipe,
    lint_payload,
    parse_ingredients,
    UnsafeURLError,
    UnsupportedPasteError,
)
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


@bp.get("/ai/status")
@login_required
def ai_status():
    """Whether a usable AI provider is configured, for the chat widget's setup
    state. Readable by any member (not just the owner who edits it). Returns the
    effective provider NAME only — never a key or URL."""
    from ..services.ai import registry
    try:
        name = registry._configured_name(registry._effective()) or ""
    except Exception:  # noqa: BLE001 - status must never 500
        name = ""
    try:
        get_provider()
        enabled = True
    except ProviderError:
        enabled = False
    return jsonify({"enabled": enabled, "provider": name})


@bp.get("/ai/chat-settings")
@login_required
def get_chat_settings():
    """This household's default for streaming chat replies. Any member reads it
    (each browser can also override on the chat widget); only the owner sets it."""
    from ..services.ai.provider_config import get_chat_stream
    return jsonify({"stream": get_chat_stream(current_group().id)})


@bp.put("/ai/chat-settings")
@owner_required
def put_chat_settings():
    from ..services.ai.provider_config import set_chat_stream
    stream = bool((request.get_json(force=True) or {}).get("stream"))
    set_chat_stream(current_group().id, stream)
    return jsonify({"stream": stream})


@bp.get("/ai/job-settings")
@login_required
def get_ai_job_settings():
    """The async-job AI preference: a provider+model default for background work,
    separate from chat. `enrich` = the nutrition job; `organize` = categorize +
    cluster. Blank provider = same as chat."""
    from ..services.ai.provider_config import get_job_prefs
    return jsonify(get_job_prefs(current_group().id))


@bp.put("/ai/job-settings")
@owner_required
def put_ai_job_settings():
    from ..services.ai.provider_config import VALID_PROVIDERS, get_job_prefs, set_job_prefs
    data = request.get_json(silent=True) or {}
    for area in ("enrich", "organize"):
        blk = data.get(area)
        if isinstance(blk, dict) and blk.get("provider"):
            if str(blk["provider"]) not in VALID_PROVIDERS:
                return jsonify({"error": f"unknown provider {blk['provider']!r}"}), 422
    set_job_prefs(current_group().id, data)
    return jsonify(get_job_prefs(current_group().id))


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

    # A non-empty base URL is SSRF-guarded: the DB path bypasses the env-layer
    # URL parser, so validate the scheme AND the resolved address here to avoid
    # storing a host a later server-side httpx call would fetch from (cloud
    # metadata / link-local). Loopback + LAN stay allowed (local Ollama/SLM).
    base_url = data.get("baseUrl")
    if base_url:
        from ..services.ai.url_guard import llm_url_ok
        ok, err = llm_url_ok(str(base_url).strip())
        if not ok:
            return jsonify({"error": err}), 422

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
    from ..services.ai.provider_config import list_models_result, probe_config
    from ..services.ai.url_guard import llm_url_ok

    data = request.get_json(silent=True) or {}
    base_url = data.get("baseUrl")
    if base_url:
        ok, err = llm_url_ok(str(base_url).strip())
        if not ok:
            return jsonify({"error": err}), 422
    eff = probe_config(_base_settings(), current_group().id,
                       provider=data.get("provider"), base_url=base_url,
                       api_key=data.get("apiKey"))
    # 200 with an `error` field, not a failure status: the picker must still
    # render (Edibl's /assistant/models does the same). An empty list and a
    # broken one are different problems and used to look identical here.
    result = list_models_result(eff)
    return jsonify({"provider": eff.AI_PROVIDER, "models": result["models"],
                    "error": result["error"]})


def _import_events(data, group_id):
    """Run an import, yielding ``(event, payload)`` as each stage completes.

    One implementation behind two endpoints: ``/ai/import`` drains this and
    returns the terminal event as JSON, ``/ai/import/stream`` writes each event
    out as it happens. Anything else would be two import paths that drift.

    The terminal event is always ``done`` or ``error`` — an error is *yielded*,
    not raised, because in the streaming case the response headers have already
    been sent and raising would leave the client hanging on a truncated body.
    """
    url = str(data.get("url") or "").strip()
    text = str(data.get("text") or "").strip()
    query = str(data.get("query") or "").strip()
    if not url and not text and not query:
        yield "error", {"error": "provide a url, text, or a recipe name to import",
                        "status": 422}
        return

    # Import by name: web-search the recipe, then import the best hit's URL.
    if query and not url and not text:
        from ..services import websearch
        if not websearch.enabled():
            yield "error", {"error": "Web search isn't configured — add an Ollama "
                                     "search key in Settings to import by name.",
                            "status": 503}
            return
        yield "stage", {"stage": "searching", "detail": query}
        results = websearch.web_search(f"{query} recipe")
        url = next((str(r.get("url") or "") for r in results if r.get("url")), "")
        if not url:
            yield "error", {"error": f"No recipe found online for “{query}”.",
                            "status": 404}
            return

    # A provider is only needed for the AI fallback; resolve leniently so a
    # JSON-LD URL import still works without one.
    provider = None
    try:
        provider = get_provider()
    except ProviderError:
        provider = None

    yield "stage", {"stage": "fetching", "detail": url or "pasted text"}
    try:
        payload = import_recipe(url=url, text=text, provider=provider)
    except UnsupportedPasteError as exc:
        # Before ValueError (it subclasses it): pasted structured data that is
        # not a Recipe. A curated message, and NOT the "no AI provider" 503 —
        # the provider was never the problem here.
        yield "error", {"error": str(exc), "status": 422}
        return
    except UnsafeURLError as exc:
        # Must be checked before ValueError — UnsafeURLError subclasses it.
        yield "error", {"error": str(exc), "status": 400}
        return
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError) as exc:
        # Also before ValueError: UnicodeError subclasses it, so this arm was
        # previously unreachable and a malformed-encoding URL was reported as
        # "no AI provider configured" (503) instead of a fetch failure (502).
        yield "error", {"error": f"could not fetch the URL: {exc}", "status": 502}
        return
    except ProviderError as exc:
        yield "error", {"error": str(exc), "status": 502}
        return
    except ValueError:
        # Reached the AI path with no provider configured.
        yield "error", {"error": "no AI provider configured for this import",
                        "status": 503}
        return

    yield "stage", {"stage": "parsing"}
    displays = [str(row.get("display") or "")
                for row in (payload.get("ingredients") or [])]

    # The structuring pass, over only the lines the deterministic parser could
    # not read. Both of these are strictly additive and fail open: with no
    # provider and no search key they do nothing and the import is byte-for-byte
    # what it was before.
    unreadable = [d for d in displays if d and ingredient_ai.needs_structuring(d)]
    proposals = {}
    if unreadable and provider is not None:
        yield "stage", {"stage": "structuring", "count": len(unreadable)}
        proposals = ingredient_ai.structure(displays, provider=provider)

    name = payload.get("name") or "Imported Recipe"
    recipe = Recipe(
        name=name,
        slug=unique_slug(Recipe, group_id, name),
        group_id=group_id,
    )
    db.session.add(recipe)
    _apply(recipe, {k: v for k, v in payload.items() if k != "name"})
    db.session.commit()

    # After the save, never before: a conversion lookup is a network call and
    # must not be able to lose the user's recipe if it hangs or fails.
    try:
        yield "stage", {"stage": "converting"}
        if conversions.learn_for_lines(displays, group_id):
            db.session.commit()
    except Exception:  # noqa: BLE001 - the recipe is already safely saved
        db.session.rollback()

    # Download the source image after the recipe has an id (filename = <id>.ext).
    # Best-effort: never fail the import over a missing/oversized image.
    if payload.get("imageUrl"):
        download_image_to_recipe(recipe, payload["imageUrl"])
        db.session.commit()

    yield "done", {**recipe_out(recipe),
                   # Proposals are transient and per-import — they live in the
                   # response for the confirmation step, not in a table.
                   "ingredientProposals": list(proposals.values()),
                   # Non-fatal observations about the source. The import
                   # succeeded; these say what was thin about it.
                   "warnings": lint_payload(payload)}


@bp.post("/ai/import")
@login_required
def import_recipe_endpoint():
    """Import a recipe from a URL or pasted text and save it to the group.

    URL imports try structured (JSON-LD) extraction first and only use the AI
    provider as a fallback, so a well-marked-up page imports even with no
    provider configured.
    """
    data = request.get_json(force=True) or {}
    for event, payload in _import_events(data, current_group().id):
        if event == "error":
            return jsonify({"error": payload["error"]}), payload["status"]
        if event == "done":
            return jsonify(payload), 201
    # Unreachable: the generator always ends with done or error.
    return jsonify({"error": "import produced no result"}), 500


@bp.post("/ai/import/stream")
@login_required
def import_recipe_stream_endpoint():
    """The same import, reporting each stage as it completes (NDJSON).

    NDJSON rather than SSE for the same reason as chat: EventSource cannot POST
    or carry an auth header. ``X-Accel-Buffering: no`` is load-bearing — behind
    HA ingress the proxy otherwise buffers the whole response and the progress
    the user is meant to see arrives all at once, at the end.
    """
    data = request.get_json(force=True) or {}
    group_id = current_group().id

    @stream_with_context
    def generate():
        try:
            for event, payload in _import_events(data, group_id):
                yield json.dumps({"type": event, **payload}) + "\n"
        except Exception:  # noqa: BLE001 - headers are already sent; must not raise
            # A curated message, never str(exc): an unexpected exception here can
            # carry a DSN or a provider's response body, and this text goes
            # straight to the browser.
            current_app.logger.exception("import stream failed")
            yield json.dumps({"type": "error", "status": 500,
                              "error": "The import failed unexpectedly. The full "
                                       "reason is in the add-on log."}) + "\n"

    return current_app.response_class(
        generate(),
        mimetype="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@bp.post("/ai/generate")
@login_required
def generate_recipe_endpoint():
    """Draft a recipe from a free-text idea and RETURN it (unsaved) so the recipe
    builder can prefill a form the user edits before saving."""
    data = request.get_json(silent=True) or {}
    prompt = str(data.get("prompt") or "").strip()[:4000]  # bound the LLM input
    if not prompt:
        return jsonify({"error": "describe the recipe you'd like"}), 422
    try:
        provider = get_provider()
    except ProviderError:
        return jsonify({"error": "no AI provider is configured"}), 503
    try:
        servings = int(data.get("servings") or 0)
    except (TypeError, ValueError):
        servings = 0
    try:
        payload = generate_recipe(
            prompt, provider, servings=servings,
            preferences=preferences_text(current_group().id),
        )
    except ProviderError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify(payload)


@bp.post("/ai/parse-ingredients")
@login_required
def parse_ingredients_endpoint():
    """Structure free-text ingredient lines (qty/unit/food/note) with the AI
    provider — the ML-style matching Mealie does — and return them (unsaved) for
    the builder to review."""
    data = request.get_json(silent=True) or {}
    lines = data.get("lines")
    if isinstance(lines, str):
        lines = lines.splitlines()
    if not isinstance(lines, list) or not any(str(x).strip() for x in lines):
        return jsonify({"error": "provide ingredient lines"}), 422
    # Bound the LLM input: at most 200 lines, 500 chars each.
    lines = [str(x)[:500] for x in lines][:200]
    try:
        provider = get_provider()
    except ProviderError:
        return jsonify({"error": "no AI provider is configured"}), 503
    try:
        result = parse_ingredients(lines, provider)
    except ProviderError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"ingredients": result})


@bp.post("/ai/nutrition/<recipe_id>")
@login_required
def estimate_nutrition_endpoint(recipe_id):
    """Estimate per-serving nutrition for one recipe from its ingredients, store
    it on the recipe, and return it."""
    from ..services.ai.nutrition import estimate_nutrition

    recipe = db.session.get(Recipe, recipe_id)
    if not recipe or recipe.group_id != current_group().id:
        return jsonify({"error": "recipe not found"}), 404
    lines = [i.display for i in recipe.ingredients]
    if not lines:
        return jsonify({"error": "add ingredients before estimating nutrition"}), 422
    try:
        provider = get_provider()
    except ProviderError:
        return jsonify({"error": "no AI provider is configured"}), 503
    try:
        nutrition = estimate_nutrition(lines, recipe.servings, provider)
    except ProviderError as exc:
        return jsonify({"error": str(exc)}), 502
    recipe.nutrition = json.dumps(nutrition) if nutrition else ""
    db.session.commit()
    return jsonify({"nutrition": nutrition or None})


_PHOTO_MEDIA = {"image/jpeg", "image/png", "image/webp"}
_MAX_PHOTO_BYTES = 10 * 1024 * 1024


@bp.post("/ai/photo")
@login_required
def photo_to_recipe_endpoint():
    """Extract a recipe from an uploaded photo (recipe card, cookbook page,
    handwritten note) using a vision-capable provider, save it, and keep the
    photo as the recipe image."""
    import base64

    from ..services.ai.recipe_import import recipe_from_image
    from .recipes import _IMAGE_EXTS, _image_path

    file = request.files.get("image") or request.files.get("file")
    if not file:
        return jsonify({"error": "no image uploaded"}), 422
    media_type = (file.mimetype or "").lower()
    if media_type not in _PHOTO_MEDIA:
        return jsonify({"error": "unsupported image type (use JPEG, PNG, or WebP)"}), 422
    data = file.read(_MAX_PHOTO_BYTES + 1)
    if not data:
        return jsonify({"error": "empty image"}), 422
    if len(data) > _MAX_PHOTO_BYTES:
        return jsonify({"error": "image too large (max 10 MB)"}), 413
    try:
        provider = get_provider()
    except ProviderError:
        return jsonify({"error": "no AI provider is configured"}), 503
    b64 = base64.standard_b64encode(data).decode()
    try:
        payload = recipe_from_image(b64, media_type, provider)
    except ProviderError as exc:
        # A provider without vision is a configuration mismatch (422), not an
        # upstream failure (502) — tell the user to pick a vision-capable model.
        if "does not support image" in str(exc):
            return jsonify({"error": str(exc)}), 422
        return jsonify({"error": str(exc)}), 502
    if not payload.get("ingredients"):
        return jsonify({"error": "could not find a recipe in that image"}), 422

    name = payload.get("name") or "Scanned recipe"
    recipe = Recipe(name=name, slug=unique_slug(Recipe, current_group().id, name),
                    group_id=current_group().id)
    db.session.add(recipe)
    _apply(recipe, {k: v for k, v in payload.items() if k != "name"})
    db.session.flush()  # need the id for the image filename
    ext = _IMAGE_EXTS.get(media_type, ".jpg")
    filename = f"{recipe.id}{ext}"
    try:
        with open(_image_path(filename), "wb") as fh:
            fh.write(data)
        recipe.image = filename
    except OSError:
        pass  # keeping the photo is best-effort; the transcription still saves
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

    from ..services.components import component_load_opts
    recipes = (db.session.query(Recipe).filter_by(group_id=gid)
               .options(*component_load_opts()).all())
    return jsonify({"suggestions": rank_recipes(recipes, inv["items"])[:limit],
                    "inventorySource": "edibl", "ediblAvailable": True})


@bp.post("/ai/use-it-up")
@login_required
def use_it_up():
    """Suggest recipes that use up soon-to-expire Edibl stock ('use it up').

    Degrades gracefully: with no Edibl connected it reports the feature as
    unavailable (200 + flag) rather than erroring. Deterministic, no provider.
    """
    from ..services.edibl import EdiblClient
    from ..services.inventory import rank_use_it_up

    gid = current_group().id
    data = request.get_json(silent=True) or {}
    try:
        days = min(max(int(data.get("days", 5) or 5), 1), 60)
    except (ValueError, TypeError):
        days = 5
    try:
        limit = min(int(data.get("limit", 10) or 10), 50)
    except (ValueError, TypeError):
        limit = 10

    inv = EdiblClient.from_settings().on_hand()
    if not inv["available"]:
        return jsonify({"suggestions": [], "expiring": [], "ediblAvailable": False,
                        "message": inv["reason"]})

    today = date.today()
    expiring = []
    for item in inv["items"]:
        dl = _days_left(item.get("expiresAt"), today)
        if dl is not None and dl <= days:
            expiring.append({**item, "daysLeft": dl})
    expiring.sort(key=lambda x: x["daysLeft"])

    # Eager-load ingredients + their food so ranking doesn't fire O(N·M) queries.
    from sqlalchemy.orm import selectinload

    from ..models import RecipeIngredient
    recipes = (
        db.session.query(Recipe)
        .filter_by(group_id=gid)
        .options(selectinload(Recipe.ingredients).selectinload(RecipeIngredient.food))
        .all()
    )
    return jsonify({
        "suggestions": rank_use_it_up(recipes, expiring)[:limit],
        "expiring": expiring,
        "ediblAvailable": True,
        "days": days,
    })


def _days_left(expires_at, today):
    """Whole days from ``today`` until ``expires_at`` (ISO date/datetime); may be
    negative (already expired). None when there is no parseable date."""
    if not expires_at:
        return None
    try:
        d = date.fromisoformat(str(expires_at)[:10])
    except ValueError:
        return None
    return (d - today).days


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
    # Always fold in the household's saved diet/allergy preferences, even when
    # the request sends none of its own.
    stored = preferences_text(gid)
    if stored:
        preferences = f"{preferences}; {stored}".strip("; ") if preferences else stored

    # Explicit planning constraints (optional).
    try:
        max_minutes = int(data.get("maxMinutes") or 0)
    except (ValueError, TypeError):
        max_minutes = 0
    raw_exclude = data.get("exclude")
    if isinstance(raw_exclude, str):
        raw_exclude = [raw_exclude]
    exclude = [str(x).strip()[:80] for x in raw_exclude if str(x).strip()][:30] \
        if isinstance(raw_exclude, list) else []

    recipes = db.session.query(Recipe).filter_by(group_id=gid).all()
    catalog = [
        {"id": r.id, "name": r.name, "tags": [t.name for t in r.tags],
         "totalMinutes": r.total_minutes or None}
        for r in recipes
    ]

    system = (
        "You are a household meal planner. Prefer recipes from the provided "
        "catalog (reference them by their exact id). You may invent a meal as "
        "a plain title when nothing fits. Respect the stated preferences and "
        "dietary constraints STRICTLY — never plan a meal containing an excluded "
        "ingredient or one that breaks the time limit. Aim for variety: do not "
        "repeat the same recipe more than twice across the plan."
    )
    constraint_lines = ""
    if max_minutes:
        constraint_lines += (
            f"Time limit: each meal must take at most {max_minutes} minutes "
            f"(use each recipe's totalMinutes; skip or invent quicker ones).\n"
        )
    if exclude:
        constraint_lines += f"Exclude these ingredients entirely: {', '.join(exclude)}.\n"
    prompt = (
        f"Plan {days} day(s) of meals for these meal types: "
        f"{', '.join(meal_types)}.\n"
        f"Servings target: {servings or 'unspecified'}.\n"
        f"Preferences/constraints: {preferences or 'none'}.\n"
        f"{constraint_lines}\n"
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
