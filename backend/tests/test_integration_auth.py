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


def test_ingress_trust_disabled_when_trusted_proxy_configured(tmp_path):
    # With a trusted reverse proxy, remote_addr is client-derived and can't be
    # trusted as the Supervisor peer — ingress headers must NOT authenticate.
    from app import create_app
    from app.config import Config

    class ProxyConfig(Config):
        DATA_DIR = str(tmp_path)
        DATABASE_URL = f"sqlite:///{tmp_path}/proxy.db"
        DISABLE_AUTH = False
        TRUSTED_PROXY_COUNT = 1
        SECRET_KEY = "test-secret-key-that-is-long-enough-32b"

    proxy_app = create_app(ProxyConfig)
    resp = proxy_app.test_client().get(
        SUMMARY,
        headers={"X-Remote-User-Id": "ha-1", "X-Remote-User-Display-Name": "Alex"},
        environ_overrides=SUP,
    )

    assert resp.status_code == 401


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
