from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import Category
from ..auth import login_required, current_group
from ..schemas.serializers import category_out
from ..utils import unique_slug

bp = Blueprint("categories", __name__)


def _get(cat_id):
    cat = db.session.get(Category, cat_id)
    if not cat or cat.group_id != current_group().id:
        abort(404)
    return cat


@bp.get("/categories")
@login_required
def list_categories():
    cats = (
        db.session.query(Category)
        .filter_by(group_id=current_group().id)
        .order_by(Category.name.asc())
        .all()
    )
    return jsonify([category_out(c) for c in cats])


@bp.post("/categories")
@login_required
def create_category():
    data = request.get_json(force=True) or {}
    name = data.get("name", "")
    cat = Category(
        name=name,
        slug=unique_slug(Category, current_group().id, name),
        description=data.get("description", ""),
        group_id=current_group().id,
    )
    db.session.add(cat)
    db.session.commit()
    return jsonify(category_out(cat)), 201


@bp.put("/categories/<cat_id>")
@login_required
def update_category(cat_id):
    cat = _get(cat_id)
    data = request.get_json(force=True) or {}
    if "name" in data:
        cat.name = data["name"]
        cat.slug = unique_slug(
            Category, current_group().id, data["name"], exclude_id=cat.id
        )
    if "description" in data:
        cat.description = data["description"]
    db.session.commit()
    return jsonify(category_out(cat))


@bp.delete("/categories/<cat_id>")
@login_required
def delete_category(cat_id):
    db.session.delete(_get(cat_id))
    db.session.commit()
    return "", 204
