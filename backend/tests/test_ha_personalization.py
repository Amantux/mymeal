"""Per-HA-user personalization + roles behind ingress.

Trust boundary: X-Remote-User-* headers are honored ONLY when the request comes
from the Supervisor ingress network. First HA user seen = owner; the rest are
members; all share one household.
"""
SUP = {"REMOTE_ADDR": "172.30.32.2"}          # Supervisor ingress source
LAN = {"REMOTE_ADDR": "192.168.1.50"}          # a direct-port client (untrusted)


def _hdr(uid, name="Alex"):
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name}


def test_ingress_headers_ignored_when_not_from_supervisor(noauth_app):
    """A forged X-Remote-User-Id from a non-Supervisor source must NOT create or
    select a user — it falls back to the shared local user."""
    c = noauth_app.test_client()
    r = c.get("/api/v1/users/self", headers=_hdr("hacker"), environ_overrides=LAN)
    assert r.status_code == 200
    body = r.get_json()["item"]
    assert body["email"] == "local@mymeal"     # the shared fallback, not "ha:hacker"


def test_first_ha_user_is_owner_second_is_member(noauth_app):
    c = noauth_app.test_client()
    a = c.get("/api/v1/users/self", headers=_hdr("alice", "Alice"),
              environ_overrides=SUP).get_json()["item"]
    b = c.get("/api/v1/users/self", headers=_hdr("bob", "Bob"),
              environ_overrides=SUP).get_json()["item"]
    assert a["isOwner"] is True and b["isOwner"] is False
    assert a["name"] == "Alice" and b["name"] == "Bob"
    assert a["groupId"] == b["groupId"]          # one shared household
    assert a["id"] != b["id"]                    # distinct identities


def test_same_ha_user_is_stable_and_name_refreshes(noauth_app):
    c = noauth_app.test_client()
    first = c.get("/api/v1/users/self", headers=_hdr("carol", "Carol"),
                  environ_overrides=SUP).get_json()["item"]
    again = c.get("/api/v1/users/self", headers=_hdr("carol", "Caroline"),
                  environ_overrides=SUP).get_json()["item"]
    assert again["id"] == first["id"]            # same user, not a duplicate
    assert again["name"] == "Caroline"           # display name kept fresh


def test_member_cannot_change_settings_owner_can(noauth_app):
    c = noauth_app.test_client()
    c.get("/api/v1/users/self", headers=_hdr("owner"), environ_overrides=SUP)   # owner
    c.get("/api/v1/users/self", headers=_hdr("member"), environ_overrides=SUP)  # member
    # member is 403 on the owner-gated config surface
    m = c.put("/api/v1/ai/settings", json={"provider": "ollama"},
              headers=_hdr("member"), environ_overrides=SUP)
    assert m.status_code == 403
    # owner succeeds
    o = c.put("/api/v1/ai/settings", json={"provider": "ollama"},
              headers=_hdr("owner"), environ_overrides=SUP)
    assert o.status_code == 200


def test_members_still_use_the_app_normally(noauth_app):
    """A member (non-owner) has full access to recipes/plan/shopping."""
    c = noauth_app.test_client()
    c.get("/api/v1/users/self", headers=_hdr("owner"), environ_overrides=SUP)
    member = _hdr("member2")
    c.get("/api/v1/users/self", headers=member, environ_overrides=SUP)
    assert c.get("/api/v1/recipes", headers=member, environ_overrides=SUP).status_code == 200
    r = c.post("/api/v1/recipes", json={"name": "Member's Soup"},
               headers=member, environ_overrides=SUP)
    assert r.status_code == 201


def test_legacy_local_owner_does_not_lock_out_first_ha_user(noauth_app):
    """Migrating from single-user mode: the synthetic local owner must not make
    the first real HA user a mere member — config would be stuck on a phantom."""
    app = noauth_app
    with app.app_context():
        from app.auth import _default_user
        _default_user()   # creates the legacy Local User (is_owner=True)
    c = app.test_client()
    first = c.get("/api/v1/users/self", headers=_hdr("real1", "Real One"),
                  environ_overrides=SUP).get_json()["item"]
    assert first["isOwner"] is True     # real HA user gets owner despite the legacy one
