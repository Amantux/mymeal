import json
import os

from flask import Blueprint, request, jsonify, abort, current_app, send_file
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Recipe, RecipeIngredient, RecipeStep, Category, Tag
from ..auth import login_required, current_group
from ..schemas.serializers import recipe_out, recipe_summary
from ..utils import unique_slug

bp = Blueprint("recipes", __name__)

_IMAGE_EXTS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _get(recipe_id) -> Recipe:
    recipe = db.session.get(Recipe, recipe_id)
    if not recipe or recipe.group_id != current_group().id:
        abort(404)
    return recipe


def _get_by_id_or_slug(ident) -> Recipe:
    gid = current_group().id
    recipe = db.session.get(Recipe, ident)
    if recipe and recipe.group_id == gid:
        return recipe
    recipe = db.session.query(Recipe).filter_by(group_id=gid, slug=ident).first()
    if not recipe:
        abort(404)
    return recipe


def _set_ingredients(recipe: Recipe, rows):
    """Replace a recipe's ingredient lines with ``rows`` (list of dicts)."""
    recipe.ingredients.clear()
    for i, row in enumerate(rows or []):
        recipe.ingredients.append(
            RecipeIngredient(
                display=row.get("display", ""),
                quantity=float(row.get("quantity") or 0),
                note=row.get("note", ""),
                section=row.get("section", ""),
                position=row.get("position", i),
                unit_id=row.get("unitId") or None,
                food_id=row.get("foodId") or None,
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


def _apply(recipe: Recipe, data: dict):
    simple = {
        "description": "description",
        "recipeYield": "recipe_yield",
        "servings": "servings",
        "prepMinutes": "prep_minutes",
        "cookMinutes": "cook_minutes",
        "totalMinutes": "total_minutes",
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
        recipe.nutrition = json.dumps(data["nutrition"]) if data["nutrition"] else ""
    if "ingredients" in data:
        _set_ingredients(recipe, data["ingredients"])
    if "steps" in data:
        _set_steps(recipe, data["steps"])
    if "categoryIds" in data:
        _set_taxonomy(recipe, "categories", Category, data["categoryIds"])
    if "tagIds" in data:
        _set_taxonomy(recipe, "tags", Tag, data["tagIds"])


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


@bp.get("/recipes/<ident>")
@login_required
def get_recipe(ident):
    return jsonify(recipe_out(_get_by_id_or_slug(ident)))


@bp.put("/recipes/<recipe_id>")
@login_required
def update_recipe(recipe_id):
    recipe = _get(recipe_id)
    _apply(recipe, request.get_json(force=True) or {})
    db.session.commit()
    return jsonify(recipe_out(recipe))


@bp.delete("/recipes/<recipe_id>")
@login_required
def delete_recipe(recipe_id):
    recipe = _get(recipe_id)
    if recipe.image:
        _remove_image_file(recipe.image)
    db.session.delete(recipe)
    db.session.commit()
    return "", 204


# --- Image ---------------------------------------------------------------
def _image_path(filename: str) -> str:
    return os.path.join(current_app.config["images_dir"](), filename)


def _remove_image_file(filename: str):
    try:
        os.remove(_image_path(filename))
    except OSError:
        pass


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
