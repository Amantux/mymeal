"""Authentication layer.

Supports two modes:

* **Enabled** (default): JWT bearer tokens issued at login. Requests must send
  ``Authorization: Bearer <token>``.
* **Disabled** (``MYMEAL_DISABLE_AUTH=true``): every request is transparently
  bound to a default user/group. This is intended for running behind Home
  Assistant ingress, which already authenticates the user.
"""
import functools
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
    """Return (creating if needed) the single local user for no-auth mode.

    The local user JOINS the shared household — the earliest-created group, the
    same one ingress users are provisioned into — rather than minting its own.
    Otherwise a machine client bound to this user (the HA integration token)
    would read a different, empty household than the real HA users populate.
    Only when the install is still empty do we create the household group.
    """
    user = db.session.query(User).filter_by(email=DEFAULT_EMAIL).first()
    if user:
        return user
    group = db.session.query(Group).order_by(Group.created_at.asc()).first()
    if group is None:
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


# Ingress requests reach the add-on FROM the Home Assistant Supervisor, whose
# address on the hassio network is 172.30.32.2. We trust the X-Remote-User-*
# identity headers ONLY from that exact peer:
#   * NOT the whole 172.30.32.0/23 — the bridge gateway (.1) is in that range,
#     and traffic to a host-PUBLISHED add-on port is SNAT'd to the gateway, so
#     a /23 check would trust forged headers from a directly-exposed port. The
#     specific-host check excludes the gateway (and every sibling add-on).
#   * We read the UNPROXIED peer, so ProxyFix rewriting remote_addr from a
#     client-supplied X-Forwarded-For (when TRUSTED_PROXY_COUNT is set) can't be
#     used to spoof the Supervisor address.
# Fail-closed: an unrecognised peer just falls back to the shared local user.
_INGRESS_SOURCE = "172.30.32.2"


def _request_from_ingress() -> bool:
    # If a reverse proxy is trusted, ProxyFix derives remote_addr from the
    # client-supplied X-Forwarded-For, so it can no longer be trusted as the
    # ingress source (an attacker could set XFF to the Supervisor address).
    # Ingress and an extra trusted proxy are mutually exclusive for identity:
    # HA ingress needs no TRUSTED_PROXY_COUNT, and a standalone-behind-proxy
    # deployment isn't behind ingress and gets no X-Remote-User-* headers anyway.
    settings = current_app.config.get("SETTINGS")
    if settings is not None and getattr(settings, "TRUSTED_PROXY_COUNT", 0):
        return False
    return request.remote_addr == _INGRESS_SOURCE


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
    # Only a REAL name header refreshes the stored name — never let the fallback
    # literal overwrite a good name on a request that happens to omit the header
    # (that would flap the name and write on every toggle).
    real_name = (request.headers.get("X-Remote-User-Display-Name")
                 or request.headers.get("X-Remote-User-Name") or "").strip()
    if user:
        if real_name and user.name != real_name:
            user.name = real_name
            db.session.commit()
        return user
    display = real_name or "Home Assistant user"

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
    """Resolve the current user from three independent sources, in order.

    The order matters: it lets a machine client (the Home Assistant integration,
    the MCP server) authenticate by token whether or not ``DISABLE_AUTH`` is set,
    while the browser keeps working behind ingress. ``DISABLE_AUTH`` then only
    controls the open fallback (step 3), not whether tokens/ingress are honored.

      1. An explicit ``Authorization: Bearer`` token — a long-lived API key or a
         login JWT. A present-but-INVALID token is a 401, never a silent
         downgrade to the shared user.
      2. A trusted Home Assistant ingress identity (``X-Remote-User-*`` from the
         Supervisor peer), provisioning the per-HA-user account.
      3. In open mode (``DISABLE_AUTH``) only: the shared local user — covering
         both a standalone open deployment and an ingress request that arrived
         without identity headers.
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header[len("Bearer "):].strip()
        # Long-lived API keys are prefixed so we can route them without a JWT
        # decode; either way an invalid token resolves to None (→ 401).
        if token.startswith(TOKEN_PREFIX):
            return _user_from_api_token(token)
        user_id = decode_token(token)
        return db.session.get(User, user_id) if user_id else None

    ingress = _ingress_user()
    if ingress is not None:
        return ingress

    if current_app.config["DISABLE_AUTH"]:
        return _default_user()

    return None


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
