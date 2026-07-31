"""API-token (API key) management for machine clients (HA integration, MCP)."""
from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import ApiToken, TOKEN_SCOPES, TOKEN_ACCESS, generate_raw_token, hash_token
from ..auth import owner_required, current_group, current_user
from ..schemas.serializers import iso

bp = Blueprint("tokens", __name__)


def _token_out(t):
    return {
        "id": t.id,
        "name": t.name,
        "hint": t.hint,
        "scope": t.scope or "full",
        "access": t.access or "write",
        "lastUsedAt": iso(t.last_used_at),
        "createdAt": iso(t.created_at),
    }


@bp.get("/tokens")
@owner_required
def list_tokens():
    tokens = (
        db.session.query(ApiToken).filter_by(group_id=current_group().id).all()
    )
    return jsonify([_token_out(t) for t in tokens])


@bp.post("/tokens")
@owner_required
def create_token_():
    data = request.get_json(silent=True) or {}
    scope = (data.get("scope") or "full").strip().lower()
    if scope not in TOKEN_SCOPES:
        return jsonify({"error": f"scope must be one of {', '.join(TOKEN_SCOPES)}"}), 400
    access = (data.get("access") or "write").strip().lower()
    if access not in TOKEN_ACCESS:
        return jsonify({"error": f"access must be one of {', '.join(TOKEN_ACCESS)}"}), 400
    raw = generate_raw_token()
    token = ApiToken(
        name=data.get("name", ""),
        token_hash=hash_token(raw),
        hint=raw[:7] + "…",
        scope=scope,
        access=access,
        user_id=current_user().id,
        group_id=current_group().id,
    )
    db.session.add(token)
    db.session.commit()
    # The raw token is shown exactly once, here.
    return jsonify({**_token_out(token), "token": raw}), 201


@bp.delete("/tokens/<token_id>")
@owner_required
def delete_token(token_id):
    token = db.session.get(ApiToken, token_id)
    if not token or token.group_id != current_group().id:
        abort(404)
    db.session.delete(token)
    db.session.commit()
    return "", 204
