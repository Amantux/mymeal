from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, current_app

from ..extensions import db
from ..models import User, Group, GroupInvitation
from ..auth import (
    login_required,
    current_user,
    hash_password,
    verify_password,
    create_token,
)
from ..schemas.serializers import user_out

bp = Blueprint("users", __name__)


def _invitation_expired(inv) -> bool:
    """True if the invitation's ``expires_at`` (ISO string) is in the past."""
    if not inv.expires_at:
        return False
    try:
        exp = datetime.fromisoformat(inv.expires_at)
    except ValueError:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp < datetime.now(timezone.utc)


@bp.post("/users/register")
def register():
    if not current_app.config["ALLOW_REGISTRATION"]:
        return jsonify({"error": "registration disabled"}), 403

    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    name = data.get("name") or ""
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"error": "email and password required"}), 422
    if db.session.query(User).filter_by(email=email).first():
        return jsonify({"error": "email already registered"}), 409

    token = data.get("token")
    if token:
        inv = (
            db.session.query(GroupInvitation).filter_by(token=token).first()
        )
        if not inv:
            return jsonify({"error": "invalid invitation token"}), 422
        # Enforce the invitation's limits — an unbounded/expired invite would
        # otherwise grant permanent access to another household's tenant.
        if _invitation_expired(inv):
            return jsonify({"error": "invitation token expired"}), 422
        if inv.uses <= 0:
            return jsonify({"error": "invitation token already used"}), 422
        inv.uses -= 1
        group = inv.group
        is_owner = False
        if inv.uses <= 0:
            db.session.delete(inv)
    else:
        group = Group(name=data.get("groupName") or f"{name}'s Kitchen")
        db.session.add(group)
        db.session.flush()
        is_owner = True

    user = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        is_owner=is_owner,
        group_id=group.id,
        activated_on=datetime.now(timezone.utc).isoformat(),
    )
    db.session.add(user)
    db.session.commit()
    return jsonify(user_out(user)), 201


@bp.post("/users/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (
        data.get("username")
        or data.get("email")
        or request.form.get("username")
        or ""
    ).strip().lower()
    password = data.get("password") or request.form.get("password") or ""
    user = db.session.query(User).filter_by(email=email).first()
    if not user or not verify_password(password, user.password_hash):
        return jsonify({"error": "invalid credentials"}), 401
    token = create_token(user)
    return jsonify({"token": f"Bearer {token}", "expiresAt": None})


@bp.get("/users/refresh")
@login_required
def refresh():
    token = create_token(current_user())
    return jsonify({"token": f"Bearer {token}", "expiresAt": None})


@bp.post("/users/logout")
@login_required
def logout():
    # Stateless JWT: client discards the token.
    return jsonify({"message": "logged out"})


@bp.get("/users/self")
@login_required
def get_self():
    return jsonify({"item": user_out(current_user())})


@bp.put("/users/self")
@login_required
def update_self():
    data = request.get_json(force=True) or {}
    user = current_user()
    if "name" in data:
        user.name = data["name"]
    if "email" in data:
        new_email = data["email"].strip().lower()
        if new_email != user.email and (
            db.session.query(User).filter_by(email=new_email).first()
        ):
            return jsonify({"error": "email already registered"}), 409
        user.email = new_email
    db.session.commit()
    return jsonify({"item": user_out(user)})


@bp.put("/users/change-password")
@login_required
def change_password():
    data = request.get_json(force=True) or {}
    user = current_user()
    if not verify_password(data.get("current", ""), user.password_hash):
        return jsonify({"error": "current password incorrect"}), 400
    user.password_hash = hash_password(data.get("new", ""))
    db.session.commit()
    return "", 204
