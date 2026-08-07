import json
import os
import uuid
import secrets
from urllib.parse import urljoin

import httpx
from flask import Blueprint, request, jsonify, abort, current_app, send_file
from sqlalchemy.orm import selectinload

from werkzeug.utils import safe_join, secure_filename

from ..extensions import db
from ..services import videos
from ..models import RecipeVideo, Recipe, RecipeIngredient, RecipeStep, Category, Tag, Unit, Food
from ..auth import login_required, current_group
from ..schemas.serializers import recipe_video_out, recipe_out, recipe_summary
from ..services import recipe_resolve
from ..utils import unique_slug

bp = Blueprint("recipes", __name__)

_IMAGE_EXTS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_INGREDIENT_ROWS = 200  # cap rows so a payload can't spawn unbounded Unit/Food


# Everything recipe_out traverses, eager-loaded so serializing a recipe is a
# handful of queries instead of one-per-ingredient (food/unit/refRecipe) N+1s.
def _recipe_load_opts():
    return (
        selectinload(Recipe.ingredients).selectinload(RecipeIngredient.food),
        selectinload(Recipe.ingredients).selectinload(RecipeIngredient.unit),
        selectinload(Recipe.ingredients).selectinload(RecipeIngredient.ref_recipe),
        selectinload(Recipe.steps),
        selectinload(Recipe.videos),
        selectinload(Recipe.tags),
        selectinload(Recipe.categories),
    )


def _get(recipe_id) -> Recipe:
    recipe = (db.session.query(Recipe)
              .options(*_recipe_load_opts())
              .filter_by(id=recipe_id).first())
    if not recipe or recipe.group_id != current_group().id:
        abort(404)
    return recipe


def _get_by_id_or_slug(ident) -> Recipe:
    gid = current_group().id
    recipe = (db.session.query(Recipe)
              .options(*_recipe_load_opts())
              .filter_by(id=ident).first())
    if recipe and recipe.group_id == gid:
        return recipe
    recipe = (db.session.query(Recipe)
              .options(*_recipe_load_opts())
              .filter_by(group_id=gid, slug=ident).first())
    if not recipe:
        abort(404)
    return recipe


def _find_or_create(model, gid: str, name: str):
    """Find (case-insensitively) or create a group-scoped ``model`` by name."""
    name = (name or "").strip()
    if not name:
        return None
    obj = (
        db.session.query(model)
        .filter(model.group_id == gid, db.func.lower(model.name) == name.lower())
        .first()
    )
    if not obj:
        obj = model(name=name, group_id=gid)
        db.session.add(obj)
        db.session.flush()
    return obj.id


def _find_or_create_unit(gid: str, name: str):
    return _find_or_create(Unit, gid, name)


class _FoodCache:
    """The group's foods, loaded ONCE per recipe save.

    ``_find_or_create_food`` used to run a full ``query(Food).filter(group_id)``
    AND fold every food's terms once PER ingredient row — O(rows × foods), a
    measured ~23 s save on a large catalog and a worker-exhaustion vector. This
    loads them once and answers exact-name and folded-term lookups in memory.

    A food created for an EARLIER row is appended (``add``), so a name repeated
    within one save resolves to the row the first occurrence created rather than
    spawning a duplicate — the correctness property the per-row re-query used to
    give for free.
    """

    def __init__(self, gid: str):
        from ..services import food_resolve
        self._fr = food_resolve
        self._by_exact: dict = {}          # lower(name) -> Food
        self._by_term: dict = {}           # folded term -> Food (first wins)
        for food in db.session.query(Food).filter_by(group_id=gid).all():
            self._add(food)

    def _add(self, food) -> None:
        self._by_exact.setdefault((food.name or "").lower(), food)
        for t in [food.name] + self._fr.aliases_of(food):
            key = self._fr.fold(self._fr.normalize_text(t))
            if key:
                self._by_term.setdefault(key, food)

    def exact(self, raw: str):
        return self._by_exact.get(raw.lower())

    def by_term(self, folded: str):
        return self._by_term.get(folded)

    def add(self, food) -> None:
        self._add(food)


def _find_or_create_food(gid: str, name: str, cache: "_FoodCache | None" = None):
    """Resolve a free-text food name to a Food id, and the variety split off it.

    Returns ``(food_id, qualifier)``.

    Order matters, and it is chosen so nothing existing churns:

    1. An exact (case-insensitive) match on what the caller typed wins. A
       household that already has a Food called "Vietnamese cinnamon" keeps
       using it — this change must not orphan rows people curated by hand.
    2. Otherwise the name is split by services.food_resolve, and an existing
       Food matching the CANONICAL half is used, carrying the variety back as
       the qualifier.
    3. Otherwise a Food is created under the canonical name — "cinnamon", not
       "Vietnamese cinnamon" — which is the whole point: one row per real
       ingredient, the variety on the line.

    ``cache`` is the per-save :class:`_FoodCache`; passing None builds a
    throwaway one (single-lookup callers). The two-pass ORDER below is
    load-bearing: a household's own name/alias (what they TYPED) beats anything
    the lexicon infers — "Chinese parsley" is coriander, but "chinese" is also a
    nationality qualifier, so the split alone would strand them on a new
    "parsley" row.
    """
    from ..services import food_resolve

    raw = (name or "").strip()
    if not raw:
        return None, ""
    if cache is None:
        cache = _FoodCache(gid)

    exact = cache.exact(raw)
    if exact:
        return exact.id, ""

    canonical, qualifier, _why = food_resolve.normalize(raw)
    canonical = canonical or raw

    typed = food_resolve.fold(food_resolve.normalize_text(raw))
    hit = cache.by_term(typed)
    if hit:
        return hit.id, ""          # matched what they typed; nothing split off

    wanted = food_resolve.fold(food_resolve.normalize_text(canonical))
    hit = cache.by_term(wanted)
    if hit:
        return hit.id, qualifier

    food = Food(name=canonical[:255], group_id=gid)
    # Stamp what we know about it, so this household's own rows can take part in
    # the material-boundary guard rather than only the shipped seed list.
    seeded = food_resolve.SEED_FOODS.get(canonical)
    if seeded:
        food.classification, food.allergens = seeded[0], list(seeded[1])
    db.session.add(food)
    db.session.flush()
    cache.add(food)                # visible to later rows in the same save
    return food.id, qualifier


def _owned_id(gid: str, model, raw):
    """Return ``raw`` only if it names a row of ``model`` in this group, else None.

    The tenancy check for a caller-supplied foodId/unitId — an id that belongs
    to another group (or doesn't exist) is dropped rather than linked, so it can
    never be read back through the serializer.
    """
    if not raw:
        return None
    rid = str(raw)
    exists = db.session.query(model.id).filter_by(id=rid, group_id=gid).first()
    return rid if exists else None


def _set_ingredients(recipe: Recipe, rows):
    """Replace a recipe's ingredient lines with ``rows`` (list of dicts).

    When a row isn't already structured (no explicit quantity/unit), parse its
    free-text ``display`` into quantity + unit — the Mealie-style structured
    ingredient — so scaling and shopping-list consolidation work. ``display``
    stays the human source of truth; the parse is best-effort (may be partial).
    """
    from ..services import units

    recipe.ingredients.clear()
    gid = recipe.group_id
    # Load the group's foods ONCE for the whole save, not once per ingredient
    # (that was an O(rows × foods) ~23 s worker-hog on a large catalog).
    food_cache = _FoodCache(gid)
    # Cap rows + clamp names so a large/adversarial payload can't spawn thousands
    # of Unit/Food rows or overflow their columns (Unit.name 120, Food.name 255)
    # — a raw string from the AI parser would otherwise 500 on Postgres.
    for i, row in enumerate((rows or [])[:MAX_INGREDIENT_ROWS]):
        display = str(row.get("display", ""))[:1000]
        qty = row.get("quantity")
        # A caller-supplied foodId/unitId must belong to THIS group, exactly
        # like refRecipeId below. Without the scope check, group A could
        # reference group B's Food/Unit by id and read its name/description/
        # aliases back through food_out — a cross-tenant disclosure. Unknown or
        # cross-group ids drop to None (the row falls back to its free-text
        # food/unit name, or none).
        unit_id = _owned_id(gid, Unit, row.get("unitId"))
        food_id = _owned_id(gid, Food, row.get("foodId"))
        # A row may LINK another recipe as a component. Validate it belongs to
        # this group and isn't the recipe itself; when set, it replaces the food
        # (a component references a recipe, not a Food).
        ref_recipe_id = None
        raw_ref = row.get("refRecipeId")
        if raw_ref and str(raw_ref) != recipe.id:
            exists = db.session.query(Recipe.id).filter_by(
                id=str(raw_ref), group_id=gid).first()
            if exists:
                ref_recipe_id = str(raw_ref)
        # Structured unit/food NAMES (e.g. from the AI ingredient parser) win.
        if not unit_id and row.get("unit"):
            name = (units.canonical_unit(row["unit"]) or str(row["unit"]))[:120]
            unit_id = _find_or_create_unit(gid, name)
        # An explicit qualifier from the caller (the import confirmation step)
        # always wins; otherwise take whatever the split produced.
        qualifier = str(row.get("qualifier") or "")[:120]
        if not ref_recipe_id and not food_id and row.get("food"):
            food_id, split_qualifier = _find_or_create_food(
                gid, str(row["food"])[:255], food_cache)
            qualifier = qualifier or split_qualifier
        # Otherwise best-effort parse the free-text display for qty + unit.
        if not ref_recipe_id and not unit_id and (qty in (None, 0, 0.0, "")) \
                and not row.get("unit") and display:
            parsed = units.parse_line(display)
            qty = parsed["qty"] or 0
            if parsed["unit"]:
                unit_id = _find_or_create_unit(gid, parsed["unit"])
        # "1/2" and "1 1/2" are what a person types into an amount box in a
        # recipe app, and a bare float() turned that into a 500 that lost the
        # whole edit. Parse it the same way an ingredient line is parsed, and
        # fall back to 0 rather than failing the request — display is the source
        # of truth, so an unparseable amount costs scaling, not the ingredient.
        if isinstance(qty, str):
            qty = units.parse_line(qty.strip())["qty"]
        try:
            qty = float(qty or 0)
        except (TypeError, ValueError):
            qty = 0.0
        recipe.ingredients.append(
            RecipeIngredient(
                display=display,
                quantity=qty,
                note=row.get("note", ""),
                qualifier=qualifier,
                section=row.get("section", ""),
                position=row.get("position", i),
                unit_id=unit_id,
                food_id=None if ref_recipe_id else food_id,
                ref_recipe_id=ref_recipe_id,
            )
        )


def _set_steps(recipe: Recipe, rows):
    recipe.steps.clear()
    for i, row in enumerate(rows or []):
        recipe.steps.append(
            RecipeStep(
                position=row.get("position", i),
                title=row.get("title", ""),
                text=row.get("text", ""),
            )
        )


def _set_taxonomy(recipe: Recipe, attr, model, ids):
    gid = current_group().id
    objs = (
        db.session.query(model)
        .filter(model.id.in_(ids or []), model.group_id == gid)
        .all()
    )
    setattr(recipe, attr, objs)


def _set_tags_by_name(recipe: Recipe, names):
    """Attach tags by NAME (for import / the assistant), find-or-creating each in
    the recipe's group. Case-insensitive, deduped, capped."""
    gid = recipe.group_id or current_group().id
    objs, seen = [], set()
    for raw in names or []:
        nm = str(raw).strip()[:255]
        if not nm or nm.lower() in seen:
            continue
        seen.add(nm.lower())
        tag = (
            db.session.query(Tag)
            .filter(Tag.group_id == gid, db.func.lower(Tag.name) == nm.lower())
            .first()
        )
        if not tag:
            tag = Tag(name=nm, slug=unique_slug(Tag, gid, nm), group_id=gid)
            db.session.add(tag)
            db.session.flush()
        objs.append(tag)
        if len(objs) >= 12:
            break
    recipe.tags = objs


def _apply(recipe: Recipe, data: dict):
    simple = {
        "description": "description",
        "recipeYield": "recipe_yield",
        "servings": "servings",
        "prepMinutes": "prep_minutes",
        "cookMinutes": "cook_minutes",
        "totalMinutes": "total_minutes",
        "cookTemperatureC": "cook_temperature_c",
        "sourceUrl": "source_url",
        "rating": "rating",
        "isFavorite": "is_favorite",
        "notes": "notes",
    }
    for key, col in simple.items():
        if key in data and data[key] is not None:
            setattr(recipe, col, data[key])

    if "name" in data and data["name"]:
        recipe.name = data["name"]
        recipe.slug = unique_slug(
            Recipe, recipe.group_id, data["name"], exclude_id=recipe.id
        )
    if "nutrition" in data:
        from ..services.ai.nutrition import sanitize as _sanitize_nutrition
        clean = _sanitize_nutrition(data["nutrition"])
        recipe.nutrition = json.dumps(clean) if clean else ""
    if "ingredients" in data:
        _set_ingredients(recipe, data["ingredients"])
    if "steps" in data:
        _set_steps(recipe, data["steps"])
    if "categoryIds" in data:
        _set_taxonomy(recipe, "categories", Category, data["categoryIds"])
    if "tagIds" in data:
        _set_taxonomy(recipe, "tags", Tag, data["tagIds"])
    if isinstance(data.get("tags"), list):
        _set_tags_by_name(recipe, data["tags"])


@bp.get("/recipes")
@login_required
def list_recipes():
    gid = current_group().id
    q = (request.args.get("q") or "").strip()
    query = db.session.query(Recipe).filter_by(group_id=gid)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(Recipe.name.ilike(like), Recipe.description.ilike(like))
        )
    if request.args.get("favorites") in {"1", "true"}:
        query = query.filter_by(is_favorite=True)
    tag = request.args.get("tag")
    if tag:
        query = query.filter(Recipe.tags.any(Tag.slug == tag))
    category = request.args.get("category")
    if category:
        query = query.filter(Recipe.categories.any(Category.slug == category))

    # Eager-load taxonomy so the summary loop doesn't fire ~2 queries per row.
    query = query.options(
        selectinload(Recipe.tags), selectinload(Recipe.categories)
    )
    recipes = query.order_by(Recipe.name.asc()).all()
    return jsonify(
        {"items": [recipe_summary(r) for r in recipes], "total": len(recipes)}
    )


@bp.post("/recipes")
@login_required
def create_recipe():
    data = request.get_json(force=True) or {}
    name = data.get("name") or "New Recipe"
    recipe = Recipe(
        name=name,
        slug=unique_slug(Recipe, current_group().id, name),
        group_id=current_group().id,
    )
    db.session.add(recipe)
    _apply(recipe, {k: v for k, v in data.items() if k != "name"})
    db.session.commit()
    return jsonify(recipe_out(recipe)), 201


# Registered before /recipes/<ident> for readability; Werkzeug prefers the
# static rule regardless of order, and a test pins that so "resolve" can never
# be swallowed as a recipe slug.
@bp.get("/recipes/resolve")
@login_required
def resolve_recipe():
    """Resolve a name/id to ONE recipe, or to the candidates worth asking about.

    The single source of truth for "did the user mean this recipe?", shared by
    the MCP server and the Home Assistant integration (both separate processes,
    so they consume it over HTTP rather than importing the helper).

    ``{"confidence": "high", "recipe": {...}, "matchedOn": ...}`` — safe to act.
    ``{"confidence": "low", "candidates": [...]}``  — ask which; do NOT act.
    ``{"confidence": "none"}`` — nothing matched.
    """
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"confidence": "none", "candidates": [],
                        "error": "No recipe name or id given."}), 400
    gid = current_group().id

    # An id or slug is an unambiguous handle — never a fuzzy match.
    direct = db.session.get(Recipe, q)
    if not direct or direct.group_id != gid:
        direct = db.session.query(Recipe).filter_by(group_id=gid, slug=q).first()
    if direct:
        return jsonify({"confidence": "high", "matchedOn": "id",
                        "recipe": recipe_out(direct)})

    like = f"%{q}%"
    rows = (
        db.session.query(Recipe)
        .filter_by(group_id=gid)
        .filter(
            db.or_(
                Recipe.name.ilike(like),
                Recipe.description.ilike(like),
                Recipe.tags.any(Tag.name.ilike(like)),
            )
        )
        .options(selectinload(Recipe.tags))
        .all()
    )
    decision = recipe_resolve.decide(
        [{"id": r.id, "name": r.name, "tags": [t.name for t in r.tags],
          "description": r.description or ""} for r in rows],
        q,
    )
    if decision["confidence"] == "high":
        match = db.session.get(Recipe, decision["match"]["id"])
        return jsonify({"confidence": "high",
                        "matchedOn": decision.get("matchedOn"),
                        "recipe": recipe_out(match)})
    return jsonify(decision)


@bp.get("/recipes/<ident>")
@login_required
def get_recipe(ident):
    recipe = _get_by_id_or_slug(ident)
    out = recipe_out(recipe)
    _apply_view(out, recipe, request.args)
    return jsonify(out)


def _apply_view(out: dict, recipe: Recipe, args):
    """Optional read-only transforms driven by query params:
    ?servings=N     → scale ingredient quantities to N servings
    ?units=weight   → show weights where a food's density is known
    Pure: mutates the response only, never the stored recipe."""
    from ..services import units

    try:
        target = int(args.get("servings") or 0)
    except (TypeError, ValueError):
        target = 0
    to_weight = str(args.get("units") or "").lower() == "weight"
    factor = (target / recipe.servings) if target > 0 and recipe.servings else 1.0
    if factor == 1.0 and not to_weight:
        return
    out["scaledServings"] = target if target > 0 else recipe.servings
    # Built once per request, not per line: it memoises its lookups, so a recipe
    # using butter four times costs one query. Only confirmed conversions are
    # visible through it, and the built-in tables are still consulted first.
    learned = None
    if to_weight:
        from ..services import conversions
        learned = conversions.resolver(recipe.group_id)
    for ing in out.get("ingredients", []):
        line = units.scale_line(ing.get("display", ""), factor)
        if to_weight:
            # Keep the original measure, append the weight in parentheses.
            line = units.annotate_weight(line, learned=learned)
        ing["display"] = line


@bp.put("/recipes/<recipe_id>")
@login_required
def update_recipe(recipe_id):
    recipe = _get(recipe_id)
    # Snapshot the pre-edit state into the auto-history timeline before overwriting,
    # then prune to the cap. (Component-only relationship traversal is already loaded.)
    from .recipe_versions import snapshot_current, prune_auto
    if recipe.ingredients or recipe.steps or recipe.name:
        # Auto-history is best-effort: a snapshot failure must NEVER block the edit.
        # A SAVEPOINT rolls back only the (half-added) version on error, leaving the
        # update to proceed on the outer transaction.
        try:
            with db.session.begin_nested():
                snapshot_current(recipe, kind="auto", label="Edit")
                prune_auto(recipe)
        except Exception:  # noqa: BLE001 — history is optional; the edit must still land
            current_app.logger.warning(
                "recipe %s: auto-snapshot failed, continuing with the edit", recipe.id)
    _apply(recipe, request.get_json(force=True) or {})
    db.session.commit()
    return jsonify(recipe_out(recipe))


@bp.delete("/recipes/<recipe_id>")
@login_required
def delete_recipe(recipe_id):
    recipe = _get(recipe_id)
    if recipe.image:
        _remove_image_file(recipe.image)
    # Detach any component links pointing at this recipe so no dangling ref is
    # left. The FK is ON DELETE SET NULL, but do it explicitly so the guarantee
    # holds even on installs whose migrated schema lacks that constraint.
    db.session.query(RecipeIngredient).filter_by(ref_recipe_id=recipe.id).update(
        {"ref_recipe_id": None}, synchronize_session=False)
    db.session.delete(recipe)
    db.session.commit()
    return "", 204


# --- Image ---------------------------------------------------------------
def _image_path(filename: str) -> str:
    # safe_join, matching _video_path: the stored name is a server-generated
    # UUID+ext today so traversal isn't currently reachable, but the repo rule
    # is safe_join even for an existence check — one hardened path, not two
    # that differ, so a future caller can't reintroduce the hole.
    path = safe_join(current_app.config["images_dir"](), filename)
    if path is None:
        abort(404)
    return path


def _remove_image_file(filename: str):
    try:
        os.remove(_image_path(filename))
    except OSError:
        # Best-effort cleanup: the image may already be gone (double delete, or
        # removed out-of-band). Failing to unlink a stale file must never break
        # the recipe delete/replace the caller actually asked for.
        pass


_MAX_IMAGE_BYTES = 8_000_000


def download_image_to_recipe(recipe: Recipe, url: str):
    """Best-effort: fetch an external image URL and store it as the recipe's
    image (used by import). SSRF-guarded per redirect hop and size/type-capped;
    any failure is swallowed so a bad image never breaks the import."""
    from ..services.ai.recipe_import import pinned_get_args

    url = (url or "").strip()
    if not url:
        return
    try:
        current = url
        with httpx.Client(follow_redirects=False, timeout=20) as client:
            for _ in range(5):
                pinned, host_hdr, conn_ext = pinned_get_args(current)
                with client.stream("GET", pinned, headers=host_hdr, extensions=conn_ext) as r:
                    if r.is_redirect and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    r.raise_for_status()
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    ext = _IMAGE_EXTS.get(ctype)
                    if not ext:
                        return  # not a supported image type
                    chunks, total = [], 0
                    for chunk in r.iter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > _MAX_IMAGE_BYTES:
                            return  # too large — skip
                    filename = f"{recipe.id}{ext}"
                    with open(_image_path(filename), "wb") as fh:
                        fh.write(b"".join(chunks))
                    recipe.image = filename
                    return
    except Exception:  # noqa: BLE001
        # Truly best-effort: a bad/oversized/unreachable image (incl. an invalid
        # URL from the imported page, which raises httpx.InvalidURL — NOT an
        # HTTPError) must never fail the import. The recipe just has no image.
        return


@bp.get("/recipes/<recipe_id>/image")
@login_required
def get_image(recipe_id):
    recipe = _get(recipe_id)
    if not recipe.image:
        abort(404)
    path = _image_path(recipe.image)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)


@bp.put("/recipes/<recipe_id>/image")
@login_required
def upload_image(recipe_id):
    recipe = _get(recipe_id)
    file = request.files.get("image") or request.files.get("file")
    if not file:
        return jsonify({"error": "no image uploaded"}), 422
    ext = _IMAGE_EXTS.get(file.mimetype, ".jpg")
    filename = f"{recipe.id}{ext}"
    file.save(_image_path(filename))
    recipe.image = filename
    db.session.commit()
    return jsonify(recipe_out(recipe))


@bp.post("/recipes/parse")
@login_required
def parse_ingredient_lines():
    """Deterministic, offline parse of free-text ingredient lines into
    structured rows (quantity / unit / food) for the builder's 'Paste list'.

    No AI: splits the leading quantity + a known unit; the remainder becomes the
    food. Fast and always available; the AI 'Structure' step refines food vs
    note. Bounded like the other ingredient inputs.
    """
    from ..services import units

    data = request.get_json(silent=True) or {}
    raw = data.get("lines")
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        return jsonify({"error": "provide ingredient lines"}), 422
    rows = []
    for line in [str(x)[:1000] for x in raw][:MAX_INGREDIENT_ROWS]:
        display = line.strip()
        if not display:
            continue
        p = units.parse_line(display)
        rows.append({
            "display": display,
            "quantity": p["qty"] or 0,
            "unit": p["unit"] or "",
            "food": p["rest"],
            "note": "",
        })
    return jsonify({"ingredients": rows})


@bp.post("/recipes/<recipe_id>/share")
@login_required
def share_recipe(recipe_id):
    """Enable a public read-only share link (idempotent — returns the existing
    token if already shared)."""
    recipe = _get(recipe_id)  # group-scoped; 404 for another group
    if not recipe.share_token:
        recipe.share_token = secrets.token_urlsafe(32)
        db.session.commit()
    return jsonify({"shareToken": recipe.share_token})


@bp.delete("/recipes/<recipe_id>/share")
@login_required
def unshare_recipe(recipe_id):
    """Revoke the share link — the old token stops resolving immediately."""
    recipe = _get(recipe_id)
    recipe.share_token = None
    db.session.commit()
    return "", 204


# --- How-to videos -------------------------------------------------------
#
# A video is EITHER a link (YouTube/Vimeo/anything http(s)) or an uploaded file
# under <DATA_DIR>/videos. Links cost nothing and cover most cases; uploads are
# for the clip you filmed yourself.

def _video_path(filename: str) -> str:
    # secure_filename at write time and a stored name we generated, so this can
    # never escape the directory. safe_join belts-and-braces it anyway.
    path = safe_join(current_app.config["videos_dir"](), filename)
    if path is None:
        abort(404)
    return path


def _get_video(recipe: Recipe, video_id: str) -> RecipeVideo:
    video = db.session.get(RecipeVideo, video_id)
    # Checked against the RECIPE we already tenant-scoped, so a video id from
    # another household 404s rather than being served.
    if not video or video.recipe_id != recipe.id:
        abort(404)
    return video


@bp.get("/recipes/<recipe_id>/videos")
@login_required
def list_videos(recipe_id):
    recipe = _get(recipe_id)
    return jsonify([recipe_video_out(v) for v in recipe.videos])


@bp.post("/recipes/<recipe_id>/videos")
@login_required
def add_video(recipe_id):
    """Add a link (JSON: {url, title}) or an upload (multipart: file, title)."""
    recipe = _get(recipe_id)
    upload = request.files.get("file")
    title = (request.form.get("title") if upload else
             (request.get_json(silent=True) or {}).get("title")) or ""

    video = RecipeVideo(recipe_id=recipe.id, group_id=recipe.group_id,
                        title=str(title)[:videos.MAX_TITLE_LENGTH].strip(),
                        position=len(recipe.videos))
    if upload:
        mime = videos.video_mime(upload.filename or "")
        if not mime:
            return jsonify({"error": "that file is not a video we can play "
                                     f"({', '.join(sorted(videos.VIDEO_MIME_BY_EXT))})"}), 422
        ext = os.path.splitext(secure_filename(upload.filename))[1].lower()
        video.filename = f"{uuid.uuid4().hex}{ext}"
        if not video.title:
            video.title = os.path.basename(upload.filename or "")[:videos.MAX_TITLE_LENGTH]
    else:
        raw = (request.get_json(silent=True) or {}).get("url", "")
        try:
            video.url = videos.normalize_url(raw)
        except videos.VideoError as exc:
            return jsonify({"error": str(exc)}), 422
        if not video.title:
            video.title = "Video"

    try:
        videos.validate(video)
    except videos.VideoError as exc:
        return jsonify({"error": str(exc)}), 422

    # Write the file only once the row is known-good, so a rejected request
    # never leaves an orphan on disk.
    if upload:
        upload.save(_video_path(video.filename))
    db.session.add(video)
    db.session.commit()
    return jsonify(recipe_video_out(video)), 201


@bp.delete("/recipes/<recipe_id>/videos/<video_id>")
@login_required
def delete_video(recipe_id, video_id):
    recipe = _get(recipe_id)
    video = _get_video(recipe, video_id)
    if video.filename:
        try:
            os.remove(_video_path(video.filename))
        except OSError:
            # Best-effort, matching the image path: the row is the source of
            # truth and a missing file must not block the delete.
            pass
    db.session.delete(video)
    db.session.commit()
    return "", 204


@bp.get("/recipes/<recipe_id>/videos/<video_id>/stream")
@login_required
def stream_video(recipe_id, video_id):
    """Serve an uploaded video for inline playback.

    conditional=True (send_file's default) answers Range requests, which is what
    lets the player seek. The Content-Type comes from our own extension
    allowlist, never from the uploaded filename — that is what stops a file
    called "clip.mp4" full of HTML executing in the app's origin.
    """
    recipe = _get(recipe_id)
    video = _get_video(recipe, video_id)
    if not video.filename:
        abort(404)  # a link has nothing to stream
    mime = videos.video_mime(video.filename)
    if not mime:
        abort(404)
    path = _video_path(video.filename)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype=mime, conditional=True)
