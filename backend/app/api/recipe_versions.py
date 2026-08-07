"""Recipe versioning: automatic edit-history snapshots + deliberate experiment
branches (edit → cook → feedback → promote). A version stores a self-contained
JSON snapshot in the same shape ``recipes._apply`` accepts, so restoring/promoting
is just ``_apply(recipe, snapshot)``. All group-scoped + IDOR-guarded."""
import json

from flask import Blueprint, request, jsonify, abort, g

from ..extensions import db
from ..models import Recipe, RecipeVersion
from ..auth import login_required, current_group
from ..schemas.serializers import recipe_out, version_out

bp = Blueprint("recipe_versions", __name__)

MAX_AUTO_VERSIONS = 20  # keep the newest N automatic snapshots per recipe


def _snapshot_recipe(recipe: Recipe) -> dict:
    """Capture a recipe's full content as an ``_apply``-shaped payload — resolved
    unit/food/tag NAMES (find-or-created on restore) and component refs by id, so
    the snapshot rebuilds the recipe even if intervening edits changed the rows."""
    return {
        "name": recipe.name,
        "description": recipe.description,
        "recipeYield": recipe.recipe_yield,
        "servings": recipe.servings,
        "prepMinutes": recipe.prep_minutes,
        "cookMinutes": recipe.cook_minutes,
        "totalMinutes": recipe.total_minutes,
        # MUST be here: _apply accepts cookTemperatureC, and any field the
        # update endpoint accepts but the snapshot omits is silently wiped when
        # a version is restored.
        "cookTemperatureC": recipe.cook_temperature_c,
        "sourceUrl": recipe.source_url,
        "rating": recipe.rating,
        "isFavorite": recipe.is_favorite,
        "notes": recipe.notes,
        "nutrition": json.loads(recipe.nutrition) if recipe.nutrition else {},
        "ingredients": [
            {
                "display": ing.display,
                "quantity": ing.quantity,
                "note": ing.note,
                # MUST be here for the same reason cookTemperatureC is: any
                # field _apply accepts but the snapshot omits is silently wiped
                # when a version is restored.
                "qualifier": ing.qualifier,
                "section": ing.section,
                "position": ing.position,
                "unit": ing.unit.name if ing.unit else "",
                "food": ing.food.name if ing.food else "",
                "refRecipeId": ing.ref_recipe_id,
            }
            for ing in recipe.ingredients
        ],
        "steps": [
            {"position": s.position, "title": s.title, "text": s.text}
            for s in recipe.steps
        ],
        "tags": [t.name for t in recipe.tags],
        "categoryIds": [c.id for c in recipe.categories],
    }


def _uid():
    user = getattr(g, "current_user", None)
    return user.id if user is not None else None


def snapshot_current(recipe, kind="auto", status="open", label="", note="",
                     parent_version_id=None) -> RecipeVersion:
    """Persist the recipe's CURRENT state as a version. Call BEFORE any overwrite."""
    v = RecipeVersion(
        recipe_id=recipe.id,
        group_id=recipe.group_id,
        kind=kind,
        status=status,
        label=label,
        note=note,
        parent_version_id=parent_version_id,
        snapshot=json.dumps(_snapshot_recipe(recipe)),
        created_by=_uid(),
    )
    db.session.add(v)
    db.session.flush()
    return v


def prune_auto(recipe):
    """Keep only the newest ``MAX_AUTO_VERSIONS`` automatic snapshots for a recipe."""
    autos = (
        db.session.query(RecipeVersion)
        .filter_by(recipe_id=recipe.id, kind="auto")
        .order_by(RecipeVersion.created_at.desc(), RecipeVersion.id.desc())
        .all()
    )
    for old in autos[MAX_AUTO_VERSIONS:]:
        db.session.delete(old)


def _restore_into(recipe, snapshot_dict):
    """Apply a snapshot back onto the live recipe, snapshotting the current state
    first so the operation is reversible. Reuses ``recipes._apply`` (find-or-create
    by name; a dangling component ref is already dropped by ``_set_ingredients``)."""
    from .recipes import _apply

    snapshot_current(recipe, kind="auto", label="Before restore")
    prune_auto(recipe)
    _apply(recipe, snapshot_dict or {})


# --- lookups (group-scoped) ------------------------------------------------
def _recipe(rid) -> Recipe:
    recipe = db.session.get(Recipe, rid)
    if not recipe or recipe.group_id != current_group().id:
        abort(404)
    return recipe


def _version(rid, vid) -> RecipeVersion:
    recipe = _recipe(rid)
    v = db.session.get(RecipeVersion, vid)
    if not v or v.recipe_id != recipe.id or v.group_id != current_group().id:
        abort(404)
    return v


# --- endpoints -------------------------------------------------------------
@bp.get("/recipes/<rid>/versions")
@login_required
def list_versions(rid):
    recipe = _recipe(rid)
    versions = (
        db.session.query(RecipeVersion)
        .filter_by(recipe_id=recipe.id)
        .order_by(RecipeVersion.created_at.desc(), RecipeVersion.id.desc())
        .all()
    )
    return jsonify({"items": [version_out(v) for v in versions]})


@bp.get("/recipes/<rid>/versions/<vid>")
@login_required
def get_version(rid, vid):
    return jsonify(version_out(_version(rid, vid), include_snapshot=True))


@bp.post("/recipes/<rid>/versions")
@login_required
def start_experiment(rid):
    """Branch an experiment from the current recipe (editable snapshot)."""
    recipe = _recipe(rid)
    data = request.get_json(silent=True) or {}
    v = snapshot_current(
        recipe, kind="experiment", status="open",
        label=str(data.get("label") or "Experiment")[:255],
        note=str(data.get("note") or ""),
    )
    db.session.commit()
    return jsonify(version_out(v, include_snapshot=True)), 201


@bp.put("/recipes/<rid>/versions/<vid>")
@login_required
def edit_version(rid, vid):
    """Edit an experiment's snapshot only — the live recipe is NOT touched."""
    v = _version(rid, vid)
    if v.kind != "experiment":
        return jsonify({"error": "only experiments are editable"}), 422
    if v.status != "open":
        return jsonify({"error": "this experiment is closed"}), 422
    data = request.get_json(force=True) or {}
    # Store the submitted recipe payload as the snapshot; keep meta fields.
    snap = {k: v_ for k, v_ in data.items()
            if k not in ("label", "note", "rating", "feedback")}
    v.snapshot = json.dumps(snap)
    if "label" in data:
        v.label = str(data["label"] or "")[:255]
    if "note" in data:
        v.note = str(data["note"] or "")
    db.session.commit()
    return jsonify(version_out(v, include_snapshot=True))


@bp.post("/recipes/<rid>/versions/<vid>/feedback")
@login_required
def set_feedback(rid, vid):
    v = _version(rid, vid)
    data = request.get_json(force=True) or {}
    if "rating" in data:
        rating = data["rating"]
        if rating in (None, ""):
            v.rating = None
        else:
            try:
                v.rating = max(0, min(5, int(rating)))
            except (TypeError, ValueError):
                return jsonify({"error": "rating must be 0-5"}), 422
    if "feedback" in data:
        v.feedback = str(data["feedback"] or "")
    db.session.commit()
    return jsonify(version_out(v))


@bp.post("/recipes/<rid>/versions/<vid>/promote")
@login_required
def promote_version(rid, vid):
    """Make the live recipe equal this version's snapshot (reversibly)."""
    recipe = _recipe(rid)
    v = _version(rid, vid)
    _restore_into(recipe, json.loads(v.snapshot or "{}"))
    if v.kind == "experiment":
        v.status = "promoted"
    db.session.commit()
    return jsonify(recipe_out(recipe))


@bp.post("/recipes/<rid>/versions/<vid>/restore")
@login_required
def restore_version(rid, vid):
    """Restore any version (auto or experiment) onto the live recipe (reversibly)."""
    recipe = _recipe(rid)
    v = _version(rid, vid)
    _restore_into(recipe, json.loads(v.snapshot or "{}"))
    db.session.commit()
    return jsonify(recipe_out(recipe))


@bp.delete("/recipes/<rid>/versions/<vid>")
@login_required
def discard_version(rid, vid):
    v = _version(rid, vid)
    db.session.delete(v)
    db.session.commit()
    return "", 204
