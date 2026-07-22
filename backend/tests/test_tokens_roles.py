"""API-key (token) management is owner-only — mirrors Edibl, matches roles.

Minting/listing/revoking machine-client keys is household config; members must
not do it."""
SUP = {"REMOTE_ADDR": "172.30.32.2"}


def _hdr(uid):
    return {"X-Remote-User-Id": uid}


def test_member_cannot_manage_api_keys_owner_can(noauth_app):
    c = noauth_app.test_client()
    c.get("/api/v1/users/self", headers=_hdr("owner"), environ_overrides=SUP)   # owner
    c.get("/api/v1/users/self", headers=_hdr("member"), environ_overrides=SUP)  # member

    # owner: full CRUD on tokens
    created = c.post("/api/v1/tokens", json={"name": "HA"},
                     headers=_hdr("owner"), environ_overrides=SUP)
    assert created.status_code == 201
    assert created.get_json()["token"]                      # raw shown once
    assert c.get("/api/v1/tokens", headers=_hdr("owner"),
                 environ_overrides=SUP).status_code == 200

    # member: 403 on every tokens operation
    assert c.get("/api/v1/tokens", headers=_hdr("member"),
                 environ_overrides=SUP).status_code == 403
    assert c.post("/api/v1/tokens", json={"name": "x"},
                  headers=_hdr("member"), environ_overrides=SUP).status_code == 403
    tid = created.get_json()["id"]
    assert c.delete(f"/api/v1/tokens/{tid}", headers=_hdr("member"),
                    environ_overrides=SUP).status_code == 403


def test_tokens_require_auth(client):
    assert client.get("/api/v1/tokens").status_code == 401
