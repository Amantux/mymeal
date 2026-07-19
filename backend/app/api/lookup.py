"""Cross-entity search: recipes, foods, and tags in one call.

Backs both the SPA's global search box and the MCP ``search_recipes`` tool.
"""
from flask import Blueprint, request, jsonify

from ..extensions import db
from ..models import Recipe, Food, Tag
from ..auth import login_required, current_group

bp = Blueprint("lookup", __name__)


def _search_recipes(gid, q, limit):
    query = db.session.query(Recipe).filter_by(group_id=gid)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Recipe.name.ilike(like),
                Recipe.description.ilike(like),
                Recipe.tags.any(Tag.name.ilike(like)),
            )
        )
    return [
        {
            "type": "recipe",
            "id": r.id,
            "name": r.name,
            "slug": r.slug,
            "image": f"/api/v1/recipes/{r.id}/image" if r.image else None,
            "totalMinutes": r.total_minutes,
        }
        for r in query.order_by(Recipe.name.asc()).limit(limit).all()
    ]


def _search_foods(gid, q, limit):
    query = db.session.query(Food).filter_by(group_id=gid)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(Food.name.ilike(like), Food.aliases.ilike(like))
        )
    return [
        {"type": "food", "id": f.id, "name": f.name, "aisle": f.aisle}
        for f in query.order_by(Food.name.asc()).limit(limit).all()
    ]


def _search_tags(gid, q, limit):
    query = db.session.query(Tag).filter_by(group_id=gid)
    if q:
        query = query.filter(Tag.name.ilike(f"%{q}%"))
    return [
        {"type": "tag", "id": t.id, "name": t.name, "slug": t.slug}
        for t in query.order_by(Tag.name.asc()).limit(limit).all()
    ]


@bp.get("/search")
@login_required
def search():
    q = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit", 25) or 25), 100)
    gid = current_group().id

    types = request.args.get("types", "recipe,food,tag").split(",")
    results = []
    if "recipe" in types:
        results += _search_recipes(gid, q, limit)
    if "food" in types:
        results += _search_foods(gid, q, limit)
    if "tag" in types:
        results += _search_tags(gid, q, limit)
    return jsonify({"results": results, "total": len(results)})
