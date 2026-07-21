"""Authentication layer.

Supports two modes:

* **Enabled** (default): JWT bearer tokens issued at login. Requests must send
  ``Authorization: Bearer <token>``.
* **Disabled** (``MYMEAL_DISABLE_AUTH=true``): every request is transparently
  bound to a default user/group. This is intended for running behind Home
  Assistant ingress, which already authenticates the user.
"""
import functools
import ipaddress
from datetime import datetime, timezone

import jwt
from flask import current_app, g, request, jsonify
from passlib.hash import bcrypt

from .extensions import db
from .models import User, Group, ApiToken, hash_token
from .models.api_token import TOKEN_PREFIX

DEFAULT_EMAIL = "local@mymeal"
DEFAULT_GROUP = "Home"


def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.verify(password, hashed)
    except (ValueError, TypeError):
        return False


def create_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + current_app.config["JWT_EXPIRES"]
    payload = {"sub": user.id, "exp": exp}
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm="HS256")


def decode_token(token: str):
    try:
        payload = jwt.decode(
            token, current_app.config["SECRET_KEY"], algorithms=["HS256"]
        )
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def _default_user() -> User:
    """Return (creating if needed) the single local user for no-auth mode."""
    user = db.session.query(User).filter_by(email=DEFAULT_EMAIL).first()
    if user:
        return user
    group = Group(name=DEFAULT_GROUP)
    db.session.add(group)
    db.session.flush()
    user = User(
        name="Local User",
        email=DEFAULT_EMAIL,
        password_hash=hash_password("unused"),
        is_owner=True,
        group_id=group.id,
    )
    db.session.add(user)
    db.session.commit()
    return user


# Home Assistant's Supervisor/ingress proxy sits on the internal hassio network.
# Ingress requests reach the add-on FROM the Supervisor, so their source IP is
# in this range. We trust the X-Remote-User-* identity headers ONLY for such
# requests — on a directly-published port a client could forge them.
_SUPERVISOR_NET = ipaddress.ip_network("172.30.32.0/23")


def _request_from_ingress() -> bool:
    try:
        return ipaddress.ip_address(request.remote_addr or "") in _SUPERVISOR_NET
    except ValueError:
        return False


def _ingress_user():
    """Resolve (provisioning if needed) the myMeal user for the Home Assistant
    user behind an ingress request.

    Trust boundary: only consult X-Remote-User-* when the request actually came
    from the Supervisor ingress proxy — otherwise a forged header could
    impersonate. Returns None when there is no trusted ingress identity, so the
    caller falls back to the shared local user.

    All HA users share one household (group); the FIRST one seen becomes owner.
    """
    if not _request_from_ingress():
        return None
    ha_id = (request.headers.get("X-Remote-User-Id") or "").strip()
    if not ha_id:
        return None

    user = db.session.query(User).filter_by(ha_user_id=ha_id).first()
    display = (request.headers.get("X-Remote-User-Display-Name")
               or request.headers.get("X-Remote-User-Name") or "Home Assistant user").strip()
    if user:
        if display and user.name != display:   # keep the display name fresh
            user.name = display
            db.session.commit()
        return user

    # Provision into the shared household. First user in the group = owner.
    group = db.session.query(Group).order_by(Group.created_at.asc()).first()
    if group is None:
        group = Group(name=DEFAULT_GROUP)
        db.session.add(group)
        db.session.flush()
    # Count owners among REAL HA users only — a legacy synthetic local user
    # (ha_user_id NULL, is_owner=True from single-user mode) must not lock the
    # first real HA user out of owner on a migrated install.
    has_owner = db.session.query(User).filter(
        User.group_id == group.id,
        User.is_owner.is_(True),
        User.ha_user_id.isnot(None),
    ).count() > 0
    user = User(
        name=display or "Home Assistant user",
        email=f"ha:{ha_id}",              # synthetic, unique; not a login email
        password_hash=hash_password("unused"),
        is_owner=not has_owner,
        ha_user_id=ha_id,
        group_id=group.id,
    )
    db.session.add(user)
    db.session.commit()
    return user


def load_current_user():
    """Resolve the current user, honoring the DISABLE_AUTH toggle."""
    if current_app.config["DISABLE_AUTH"]:
        # Behind ingress each HA user gets their own identity; fall back to the
        # shared local user only when there's no trusted ingress identity.
        return _ingress_user() or _default_user()

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):].strip()
    # Long-lived API keys are prefixed so we can route them without a JWT decode.
    if token.startswith(TOKEN_PREFIX):
        return _user_from_api_token(token)
    user_id = decode_token(token)
    if not user_id:
        return None
    return db.session.get(User, user_id)


def _user_from_api_token(raw: str):
    record = (
        db.session.query(ApiToken).filter_by(token_hash=hash_token(raw)).first()
    )
    if record is None:
        return None
    # Record usage, but at most once a minute to avoid a write on every request.
    now = datetime.utcnow()
    if record.last_used_at is None or (now - record.last_used_at).total_seconds() > 60:
        record.last_used_at = now
        db.session.commit()
    return db.session.get(User, record.user_id)


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        user = load_current_user()
        if user is None:
            return jsonify({"error": "unauthorized"}), 401
        g.current_user = user
        g.current_group = user.group
        return fn(*args, **kwargs)

    return wrapper


def owner_required(fn):
    """Like login_required, but 403s a non-owner. For household config that
    members shouldn't change (AI provider, Edibl connection, user management)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        user = load_current_user()
        if user is None:
            return jsonify({"error": "unauthorized"}), 401
        if not user.is_owner:
            return jsonify({"error": "owner privileges required"}), 403
        g.current_user = user
        g.current_group = user.group
        return fn(*args, **kwargs)

    return wrapper


def current_user() -> User:
    return g.current_user


def current_group() -> Group:
    return g.current_group
