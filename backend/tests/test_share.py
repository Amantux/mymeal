"""Public recipe share links (feature #10): enable, revoke, public read, IDOR."""


def _make_recipe(client, name="Sharable"):
    return client.post(
        "/api/v1/recipes",
        json={"name": name, "ingredients": [{"display": "1 egg"}],
              "steps": [{"text": "Fry it"}]},
    ).get_json()


def _token(client, email):
    client.post("/api/v1/users/register",
                json={"email": email, "password": "password", "name": email})
    return client.post("/api/v1/users/login",
                       json={"username": email, "password": "password"}).get_json()["token"]


def test_share_creates_token_and_is_idempotent(auth_client):
    rid = _make_recipe(auth_client)["id"]
    t1 = auth_client.post(f"/api/v1/recipes/{rid}/share").get_json()["shareToken"]
    assert t1 and len(t1) >= 32
    t2 = auth_client.post(f"/api/v1/recipes/{rid}/share").get_json()["shareToken"]
    assert t1 == t2  # re-sharing returns the same token


def test_public_endpoint_returns_recipe_by_token(auth_client):
    rid = _make_recipe(auth_client, "Public Pie")["id"]
    token = auth_client.post(f"/api/v1/recipes/{rid}/share").get_json()["shareToken"]

    # No auth header needed for the public route.
    anon = auth_client.application.test_client()
    body = anon.get(f"/api/v1/public/recipes/{token}").get_json()
    assert body["name"] == "Public Pie"
    assert body["ingredients"] == ["1 egg"]
    # Must not leak internals.
    assert "group_id" not in body and "groupId" not in body and "id" not in body


def test_public_endpoint_404_for_unknown_or_empty_token(auth_client):
    anon = auth_client.application.test_client()
    assert anon.get("/api/v1/public/recipes/nope").status_code == 404


def test_revoked_token_stops_resolving(auth_client):
    rid = _make_recipe(auth_client)["id"]
    token = auth_client.post(f"/api/v1/recipes/{rid}/share").get_json()["shareToken"]
    anon = auth_client.application.test_client()
    assert anon.get(f"/api/v1/public/recipes/{token}").status_code == 200

    assert auth_client.delete(f"/api/v1/recipes/{rid}/share").status_code == 204
    assert anon.get(f"/api/v1/public/recipes/{token}").status_code == 404
    # And the owner's detail view now reports it unshared.
    assert auth_client.get(f"/api/v1/recipes/{rid}").get_json()["shareToken"] is None


def test_cannot_share_another_groups_recipe(client):
    a = _token(client, "owner@a.com")
    client.environ_base["HTTP_AUTHORIZATION"] = a
    rid = _make_recipe(client, "Mine")["id"]

    b = _token(client, "intruder@b.com")
    client.environ_base["HTTP_AUTHORIZATION"] = b
    assert client.post(f"/api/v1/recipes/{rid}/share").status_code == 404
    assert client.delete(f"/api/v1/recipes/{rid}/share").status_code == 404
