"""Cross-entity search: recipes, foods, and tags in one call.

Backs both the SPA's global search box and the MCP ``search_recipes`` tool.
"""
import sqlalchemy as sa
from flask import Blueprint, request, jsonify
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Recipe, Food, Tag
from ..auth import login_required, current_group
from ..services import recipe_resolve

bp = Blueprint("lookup", __name__)


# Bound the ranked candidate set: rank-then-limit must still not load an
# unbounded catalog into memory. Household search sets are far below this.
_SEARCH_CANDIDATE_CAP = 500


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
    # SQL narrows the set; relevance is scored in Python because it depends on
    # WHERE the match landed (name vs tag vs description), which the LIKE can't
    # tell us. Ordering by name alone made "chicken" resolve to whatever sorted
    # first, so callers were effectively guessing.
    query = query.options(selectinload(Recipe.tags))
    # Rank BEFORE limiting: a pre-rank `.limit(limit)` ordered by name dropped
    # the best match whenever it sorted late (an exact-name hit cut by 5 filler
    # rows). Load the matching set (bounded so a huge catalog can't OOM), rank
    # in Python, then take the top `limit`. Group-scoped + LIKE-filtered, so the
    # candidate set is small in practice.
    rows = [
        {
            "type": "recipe",
            "id": r.id,
            "name": r.name,
            "slug": r.slug,
            "image": f"/api/v1/recipes/{r.id}/image" if r.image else None,
            "totalMinutes": r.total_minutes,
            "tags": [t.name for t in r.tags],
            "description": r.description or "",
        }
        for r in query.order_by(Recipe.name.asc()).limit(_SEARCH_CANDIDATE_CAP).all()
    ]
    ranked = recipe_resolve.rank(rows, q)[:limit]
    out = []
    for row, score, matched_on in ranked:
        row["matchedOn"] = matched_on
        row["score"] = score
        text = (row.get("description") or "").strip()
        if len(text) > recipe_resolve.DESCRIPTION_SNIPPET:
            text = text[: recipe_resolve.DESCRIPTION_SNIPPET].rstrip() + "…"
        row["description"] = text
        out.append(row)
    return out


def _search_foods(gid, q, limit):
    query = db.session.query(Food).filter_by(group_id=gid)
    if q:
        like = f"%{q}%"
        query = query.filter(
            # aliases is JSON now; Postgres has no ILIKE for json, so search
            # its text form. SQLite stores JSON as TEXT and is unaffected.
            db.or_(Food.name.ilike(like),
                   sa.cast(Food.aliases, sa.Text).ilike(like))
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
    try:
        limit = min(int(request.args.get("limit", 25) or 25), 100)
    except ValueError:
        limit = 25
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
