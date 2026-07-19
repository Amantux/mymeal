import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request, jsonify

from ..extensions import db
from ..models import GroupInvitation
from ..auth import login_required, current_group, current_user
from ..schemas.serializers import group_out

bp = Blueprint("groups", __name__)


@bp.get("/groups/self")
@login_required
def get_group():
    return jsonify(group_out(current_group()))


@bp.put("/groups/self")
@login_required
def update_group():
    data = request.get_json(force=True) or {}
    group = current_group()
    if "name" in data:
        group.name = data["name"]
    db.session.commit()
    return jsonify(group_out(group))


@bp.post("/groups/invitations")
@login_required
def create_invitation():
    if not current_user().is_owner:
        return jsonify({"error": "only the owner can invite"}), 403
    data = request.get_json(silent=True) or {}
    try:
        days = int(data.get("days", 7) or 7)
        uses = int(data.get("uses", 1) or 1)
    except (ValueError, TypeError):
        return jsonify({"error": "days and uses must be integers"}), 422
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    inv = GroupInvitation(
        token=secrets.token_urlsafe(16),
        expires_at=expires.isoformat(),
        uses=max(uses, 1),
        group_id=current_group().id,
    )
    db.session.add(inv)
    db.session.commit()
    return jsonify({"token": inv.token, "expiresAt": inv.expires_at}), 201
