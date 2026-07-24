"""Medications / vitamins module: CRUD, validation, and per-user privacy."""


def _make(client, **over):
    body = {"name": "Vitamin D", "kind": "vitamin", "doseAmount": 1000,
            "doseUnit": "IU", "frequency": "daily", "timesPerDay": 1, **over}
    return client.post("/api/v1/medications", json=body)


def test_create_and_list_medication(auth_client):
    r = _make(auth_client, withFood=True, notes="morning")
    assert r.status_code == 201
    m = r.get_json()
    assert m["name"] == "Vitamin D" and m["doseAmount"] == 1000 and m["withFood"] is True

    items = auth_client.get("/api/v1/medications").get_json()["items"]
    assert len(items) == 1 and items[0]["id"] == m["id"]


def test_update_and_delete_medication(auth_client):
    mid = _make(auth_client).get_json()["id"]
    upd = auth_client.put(f"/api/v1/medications/{mid}",
                          json={"doseAmount": 2000, "frequency": "weekly", "active": False})
    assert upd.status_code == 200 and upd.get_json()["doseAmount"] == 2000
    assert auth_client.delete(f"/api/v1/medications/{mid}").status_code == 204
    assert auth_client.get("/api/v1/medications").get_json()["items"] == []


def test_name_required_and_values_sanitized(auth_client):
    assert _make(auth_client, name="").status_code == 422
    m = _make(auth_client, kind="bogus", frequency="hourly", timesPerDay=99).get_json()
    assert m["kind"] == "medication"        # unknown kind → default
    assert m["frequency"] == "daily"        # unknown frequency → default
    assert m["timesPerDay"] == 24           # clamped to the 1..24 range


def test_medications_are_private_per_user(app, auth_client):
    # auth_client is user A. Create a med, then a SECOND user must not see/touch it.
    mid = _make(auth_client).get_json()["id"]

    b = app.test_client()
    b.post("/api/v1/users/register", json={"email": "b@b.com", "password": "password", "name": "B"})
    tok = b.post("/api/v1/users/login", json={"username": "b@b.com", "password": "password"}).get_json()["token"]
    b.environ_base["HTTP_AUTHORIZATION"] = tok  # login returns a 'Bearer …' token

    assert b.get("/api/v1/medications").get_json()["items"] == []          # not in B's list
    assert b.get(f"/api/v1/medications/{mid}").status_code in (404, 405)   # no read route → own-only PUT/DEL 404
    assert b.put(f"/api/v1/medications/{mid}", json={"doseAmount": 5}).status_code == 404
    assert b.delete(f"/api/v1/medications/{mid}").status_code == 404
    # A's medication is untouched.
    assert auth_client.get("/api/v1/medications").get_json()["items"][0]["doseAmount"] == 1000
