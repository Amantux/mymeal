"""Owner can promote/demote household members to admin (owner). Owner-only,
last-owner protected, group-scoped."""
SUP = {"REMOTE_ADDR": "172.30.32.2"}


def _hdr(uid):
    return {"X-Remote-User-Id": uid}


def _self(c, uid):
    return c.get("/api/v1/users/self", headers=_hdr(uid), environ_overrides=SUP).get_json()["item"]


def test_owner_promotes_member_to_admin(noauth_app):
    c = noauth_app.test_client()
    owner = _self(c, "owner")
    member = _self(c, "member")
    assert owner["isOwner"] and not member["isOwner"]

    r = c.put(f"/api/v1/users/{member['id']}/role", json={"isOwner": True},
              headers=_hdr("owner"), environ_overrides=SUP)
    assert r.status_code == 200 and r.get_json()["isOwner"] is True
    # promoted member can now reach owner-gated config
    assert c.put("/api/v1/ai/settings", json={"provider": "ollama"},
                 headers=_hdr("member"), environ_overrides=SUP).status_code == 200


def test_member_cannot_promote_anyone(noauth_app):
    c = noauth_app.test_client()
    _self(c, "owner")
    member = _self(c, "member")
    r = c.put(f"/api/v1/users/{member['id']}/role", json={"isOwner": True},
              headers=_hdr("member"), environ_overrides=SUP)
    assert r.status_code == 403        # members can't self-promote


def test_cannot_remove_the_last_admin(noauth_app):
    c = noauth_app.test_client()
    owner = _self(c, "solo")
    r = c.put(f"/api/v1/users/{owner['id']}/role", json={"isOwner": False},
              headers=_hdr("solo"), environ_overrides=SUP)
    assert r.status_code == 409        # would leave zero admins


def test_owner_can_demote_a_second_admin(noauth_app):
    c = noauth_app.test_client()
    _self(c, "owner")
    member = _self(c, "member")
    c.put(f"/api/v1/users/{member['id']}/role", json={"isOwner": True},
          headers=_hdr("owner"), environ_overrides=SUP)
    # now two owners; demoting one is allowed
    r = c.put(f"/api/v1/users/{member['id']}/role", json={"isOwner": False},
              headers=_hdr("owner"), environ_overrides=SUP)
    assert r.status_code == 200 and r.get_json()["isOwner"] is False


def test_list_household_is_owner_only(noauth_app):
    c = noauth_app.test_client()
    _self(c, "owner")
    _self(c, "member")
    assert c.get("/api/v1/users", headers=_hdr("owner"), environ_overrides=SUP).status_code == 200
    assert c.get("/api/v1/users", headers=_hdr("member"), environ_overrides=SUP).status_code == 403


def test_cannot_change_role_across_households(app):
    """Group scoping: an owner can't touch a user in another group."""
    from app.models import User, Group
    from app.extensions import db
    with app.app_context():
        g2 = Group(name="Other")
        db.session.add(g2)
        db.session.flush()
        outsider = User(name="Out", email="out@x", password_hash="x", group_id=g2.id)
        db.session.add(outsider)
        db.session.commit()
        oid = outsider.id
    c = app.test_client()
    # register an owner in the default group (standalone path)
    c.post("/api/v1/users/register", json={"email": "own@x", "password": "password12", "name": "Own"})
    tok = c.post("/api/v1/users/login", json={"username": "own@x", "password": "password12"}).get_json()["token"]
    r = c.put(f"/api/v1/users/{oid}/role", json={"isOwner": True},
              headers={"Authorization": tok})
    assert r.status_code == 404       # not in my household
