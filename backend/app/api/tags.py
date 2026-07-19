from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import Tag
from ..auth import login_required, current_group
from ..schemas.serializers import tag_out
from ..utils import unique_slug

bp = Blueprint("tags", __name__)


def _get(tag_id):
    tag = db.session.get(Tag, tag_id)
    if not tag or tag.group_id != current_group().id:
        abort(404)
    return tag


@bp.get("/tags")
@login_required
def list_tags():
    tags = (
        db.session.query(Tag)
        .filter_by(group_id=current_group().id)
        .order_by(Tag.name.asc())
        .all()
    )
    return jsonify([tag_out(t) for t in tags])


@bp.post("/tags")
@login_required
def create_tag():
    data = request.get_json(force=True) or {}
    name = data.get("name", "")
    tag = Tag(
        name=name,
        slug=unique_slug(Tag, current_group().id, name),
        color=data.get("color", ""),
        group_id=current_group().id,
    )
    db.session.add(tag)
    db.session.commit()
    return jsonify(tag_out(tag)), 201


@bp.put("/tags/<tag_id>")
@login_required
def update_tag(tag_id):
    tag = _get(tag_id)
    data = request.get_json(force=True) or {}
    if "name" in data:
        tag.name = data["name"]
        tag.slug = unique_slug(Tag, current_group().id, data["name"], exclude_id=tag.id)
    if "color" in data:
        tag.color = data["color"]
    db.session.commit()
    return jsonify(tag_out(tag))


@bp.delete("/tags/<tag_id>")
@login_required
def delete_tag(tag_id):
    db.session.delete(_get(tag_id))
    db.session.commit()
    return "", 204
