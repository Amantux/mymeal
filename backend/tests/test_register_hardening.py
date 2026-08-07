"""Invitation single-use + change-password length bound (E2E audit nits)."""


def _owner(client, email="owner@t.com"):
    client.post("/api/v1/users/register",
                json={"email": email, "password": "password", "name": "O",
                      "groupName": "H"})
    tok = client.post("/api/v1/users/login",
                      json={"username": email, "password": "password"}).get_json()["token"]
    client.environ_base["HTTP_AUTHORIZATION"] = tok
    return client


def _make_single_use_invite(client):
    r = client.post("/api/v1/groups/invitations", json={"uses": 1})
    assert r.status_code == 201, r.status_code
    return r.get_json()["token"]


def test_a_single_use_invite_seats_exactly_one_member(client):
    """The observable security property. (The lost-update RACE itself is not
    deterministically reproducible in an in-process SQLite test — the atomic
    conditional UPDATE that prevents it is exercised directly below.)"""
    tok = _make_single_use_invite(_owner(client))

    fresh = client.application.test_client()
    r1 = fresh.post("/api/v1/users/register",
                    json={"email": "a@t.com", "password": "password",
                          "name": "A", "token": tok})
    r2 = fresh.post("/api/v1/users/register",
                    json={"email": "b@t.com", "password": "password",
                          "name": "B", "token": tok})
    assert r1.status_code == 201
    assert r2.status_code == 422   # second use refused


def test_the_atomic_consume_returns_zero_rows_on_a_spent_invite(app):
    """Directly exercise the primitive the race fix relies on: a conditional
    UPDATE ... WHERE uses > 0 returns 0 affected rows once uses is spent, which
    is how a losing concurrent registration is detected."""
    from app.extensions import db
    from app.models import Group, GroupInvitation
    with app.app_context():
        g = Group(name="G")
        db.session.add(g)
        db.session.flush()
        from datetime import datetime, timedelta, timezone
        inv = GroupInvitation(
            token="t-spent", group_id=g.id, uses=1,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat())
        db.session.add(inv)
        db.session.commit()

        first = (db.session.query(GroupInvitation)
                 .filter(GroupInvitation.id == inv.id, GroupInvitation.uses > 0)
                 .update({GroupInvitation.uses: GroupInvitation.uses - 1}))
        second = (db.session.query(GroupInvitation)
                  .filter(GroupInvitation.id == inv.id, GroupInvitation.uses > 0)
                  .update({GroupInvitation.uses: GroupInvitation.uses - 1}))
        assert first == 1 and second == 0


def test_change_password_rejects_an_overlong_new_password(client):
    c = _owner(client, "cp@t.com")
    r = c.put("/api/v1/users/change-password",
              json={"current": "password", "new": "a" * 100000})
    assert r.status_code == 422
    # the real password still works
    assert client.application.test_client().post(
        "/api/v1/users/login",
        json={"username": "cp@t.com", "password": "password"}).status_code == 200
