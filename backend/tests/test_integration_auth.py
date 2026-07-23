"""Auth-resolution + integration-token behaviour for the companion integration.

The integration polls the REST API directly (not via ingress), so a Bearer API
key must authenticate it in EVERY mode, and a minted token must be stable.
"""
from app.integration_token import TOKEN_NAME, ensure_integration_token

SUMMARY = "/api/v1/ha/summary"
SUP = {"REMOTE_ADDR": "172.30.32.2"}  # Supervisor ingress source
LAN = {"REMOTE_ADDR": "192.168.1.50"}  # untrusted direct-port client


def _mint_key(client) -> str:
    """Mint an API key through the owner-only endpoint (default user is owner
    under DISABLE_AUTH) and return the raw token."""
    resp = client.post("/api/v1/tokens", json={"name": "hass"})
    assert resp.status_code == 201
    return resp.get_json()["token"]


# --- Bearer is authoritative even when auth is "disabled" -------------------

def test_invalid_api_key_under_disable_auth_returns_401(noauth_app):
    # Regression guard: a present-but-invalid Bearer must NOT silently downgrade
    # to the shared user just because DISABLE_AUTH is set.
    client = noauth_app.test_client()

    resp = client.get(SUMMARY, headers={"Authorization": "Bearer mm_not_a_real_key"})

    assert resp.status_code == 401


def test_valid_api_key_under_disable_auth_returns_200(noauth_app):
    client = noauth_app.test_client()
    raw = _mint_key(client)

    resp = client.get(SUMMARY, headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 200


# --- Ingress identity works independently of the DISABLE_AUTH toggle --------

def test_ingress_identity_honored_when_auth_enabled(client):
    # DISABLE_AUTH is False here; a trusted-peer ingress request now resolves an
    # identity (previously it was a flat 401 without a Bearer token).
    resp = client.get(
        SUMMARY,
        headers={"X-Remote-User-Id": "ha-abc", "X-Remote-User-Display-Name": "Alex"},
        environ_overrides=SUP,
    )

    assert resp.status_code == 200


def test_forged_ingress_headers_from_untrusted_peer_rejected(client):
    # Same headers from a non-Supervisor address must not authenticate.
    resp = client.get(
        SUMMARY,
        headers={"X-Remote-User-Id": "ha-abc", "X-Remote-User-Display-Name": "Alex"},
        environ_overrides=LAN,
    )

    assert resp.status_code == 401


# --- Minted integration token ----------------------------------------------

def test_ensure_integration_token_is_stable(app):
    first = ensure_integration_token(app)
    second = ensure_integration_token(app)

    assert first and first.startswith("mm_")
    assert first == second  # reused, not rotated on the second call


def test_ensure_integration_token_authenticates(app):
    raw = ensure_integration_token(app)
    client = app.test_client()

    resp = client.get(SUMMARY, headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 200


def test_integration_token_is_named_and_revocable(app):
    ensure_integration_token(app)
    from app.extensions import db
    from app.models import ApiToken

    with app.app_context():
        rows = db.session.query(ApiToken).filter_by(name=TOKEN_NAME).all()

    assert len(rows) == 1  # exactly one, and it shows up in Settings → API keys


def test_integration_token_binds_to_ingress_household(client, app):
    # Regression for the group-divergence bug: an HA user provisioned via ingress
    # FIRST creates the household group; the integration token minted afterwards
    # must land in that SAME group, or the integration reads an empty household.
    client.get(  # provision the ingress user (and its group) first
        SUMMARY,
        headers={"X-Remote-User-Id": "ha-1", "X-Remote-User-Display-Name": "Alex"},
        environ_overrides=SUP,
    )
    raw = ensure_integration_token(app)

    from app.extensions import db
    from app.models import ApiToken, Group, User, hash_token

    with app.app_context():
        ingress_user = db.session.query(User).filter_by(ha_user_id="ha-1").first()
        token = db.session.query(ApiToken).filter_by(token_hash=hash_token(raw)).first()

        assert db.session.query(Group).count() == 1  # no second household minted
        assert token.group_id == ingress_user.group_id


def _proxy_app(tmp_path):
    """App with ProxyFix active (a trusted reverse proxy in front)."""
    from app import create_app
    from app.config import Config

    class ProxyConfig(Config):
        DATA_DIR = str(tmp_path)
        DATABASE_URL = f"sqlite:///{tmp_path}/proxy.db"
        DISABLE_AUTH = False
        TRUSTED_PROXY_COUNT = 1
        SECRET_KEY = "test-secret-key-that-is-long-enough-32b"

    return create_app(ProxyConfig)


def test_ingress_identity_honored_under_proxyfix(tmp_path):
    # ProxyFix is active AND actually rewrites remote_addr (a legit client hop in
    # X-Forwarded-For), but the real TCP peer IS the Supervisor — identity must
    # still resolve off the unproxied peer. Load-bearing: if _raw_peer reverted
    # to request.remote_addr it would read the rewritten client IP → 401.
    resp = _proxy_app(tmp_path).test_client().get(
        SUMMARY,
        headers={
            "X-Forwarded-For": "203.0.113.9",  # the browser behind ingress
            "X-Remote-User-Id": "ha-1",
            "X-Remote-User-Display-Name": "Alex",
        },
        environ_overrides=SUP,  # real TCP peer = the Supervisor
    )
    assert resp.status_code == 200


def test_forged_xforwarded_for_supervisor_rejected(tmp_path):
    # Load-bearing: ProxyFix rewrites remote_addr from a spoofed X-Forwarded-For,
    # but the check reads the UNPROXIED peer — a LAN client forging the Supervisor
    # address is NOT trusted. Fails loudly if _raw_peer reverts to remote_addr.
    resp = _proxy_app(tmp_path).test_client().get(
        SUMMARY,
        headers={
            "X-Forwarded-For": "172.30.32.2",  # spoofed Supervisor address
            "X-Remote-User-Id": "ha-evil",
            "X-Remote-User-Display-Name": "Mallory",
        },
        environ_overrides=LAN,
    )
    assert resp.status_code == 401


def _race(app, request_kwargs, n=8):
    """Fire n barrier-synchronized parallel requests; return their status codes.
    The barrier maximizes the overlap that triggers the provisioning collision."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(n)

    def hit(_):
        client = app.test_client()
        barrier.wait()
        return client.get(SUMMARY, **request_kwargs).status_code

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(hit, range(n)))


def test_default_user_provisioning_is_concurrency_safe(noauth_app):
    from app.auth import DEFAULT_EMAIL
    from app.extensions import db
    from app.models import User

    codes = _race(noauth_app, {})

    assert codes == [200] * len(codes)  # the UNIQUE(email) race must not 500
    with noauth_app.app_context():
        assert db.session.query(User).filter_by(email=DEFAULT_EMAIL).count() == 1


def test_ingress_user_provisioning_is_concurrency_safe(noauth_app):
    from app.extensions import db
    from app.models import User

    kwargs = {
        "headers": {"X-Remote-User-Id": "ha-race", "X-Remote-User-Display-Name": "R"},
        "environ_overrides": SUP,
    }
    codes = _race(noauth_app, kwargs)

    assert codes == [200] * len(codes)
    with noauth_app.app_context():
        assert db.session.query(User).filter_by(ha_user_id="ha-race").count() == 1


def test_ingress_user_collision_rolls_back_and_rereads(noauth_app):
    # Deterministic guard for the except-branch (the barrier tests above assert
    # the outcome but can't force the collision). Inject the "winning" row
    # mid-flow — during hash_password, which runs AFTER the handler's existence
    # SELECT but BEFORE its commit — so the handler's own insert hits the
    # UNIQUE(email) and must roll back + re-read rather than 500.
    from sqlalchemy.orm import Session
    from app import auth as authmod
    from app.extensions import db
    from app.models import User

    client = noauth_app.test_client()
    # Seed the household first so the racer collides only on the USER row.
    client.get(SUMMARY, headers={"X-Remote-User-Id": "seed"}, environ_overrides=SUP)
    with noauth_app.app_context():
        gid = db.session.query(User).filter_by(ha_user_id="seed").first().group_id

    real_hash = authmod.hash_password
    injected = {"done": False}

    def injecting_hash(pw):
        if not injected["done"]:
            injected["done"] = True
            s = Session(bind=db.engine)  # independent connection = the "winner"
            try:
                s.add(User(name="Winner", email="ha:racer", password_hash="x",
                           is_owner=False, ha_user_id="racer", group_id=gid))
                s.commit()
            finally:
                s.close()
        return real_hash(pw)

    authmod.hash_password = injecting_hash
    try:
        resp = client.get(
            SUMMARY, headers={"X-Remote-User-Id": "racer"}, environ_overrides=SUP
        )
    finally:
        authmod.hash_password = real_hash

    assert injected["done"]  # the except path was actually reached
    assert resp.status_code == 200  # rolled back + re-read, did not 500
    with noauth_app.app_context():
        assert db.session.query(User).filter_by(ha_user_id="racer").count() == 1


def test_valid_jwt_authenticates_through_reordered_branch(client):
    client.post(
        "/api/v1/users/register",
        json={"email": "j@j.com", "password": "password", "name": "J"},
    )
    # The login endpoint returns the token already prefixed with "Bearer ".
    jwt = client.post(
        "/api/v1/users/login", json={"username": "j@j.com", "password": "password"}
    ).get_json()["token"]

    resp = client.get("/api/v1/users/self", headers={"Authorization": jwt})

    assert resp.status_code == 200
    assert resp.get_json()["item"]["email"] == "j@j.com"
