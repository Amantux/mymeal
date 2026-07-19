def test_status_public(client):
    r = client.get("/api/v1/status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["health"] is True
    assert body["title"] == "myMeal"


def test_requires_auth(client):
    assert client.get("/api/v1/recipes").status_code == 401


def test_register_login_self(client):
    assert (
        client.post(
            "/api/v1/users/register",
            json={"email": "a@b.com", "password": "pw12345", "name": "A"},
        ).status_code
        == 201
    )
    tok = client.post(
        "/api/v1/users/login", json={"username": "a@b.com", "password": "pw12345"}
    ).get_json()["token"]
    r = client.get("/api/v1/users/self", headers={"Authorization": tok})
    assert r.get_json()["item"]["email"] == "a@b.com"


def test_bad_login(client):
    client.post(
        "/api/v1/users/register",
        json={"email": "a@b.com", "password": "pw12345", "name": "A"},
    )
    assert (
        client.post(
            "/api/v1/users/login", json={"username": "a@b.com", "password": "wrong"}
        ).status_code
        == 401
    )


def test_recipe_crud(auth_client):
    cat = auth_client.post(
        "/api/v1/categories", json={"name": "Dinner"}
    ).get_json()
    tag = auth_client.post("/api/v1/tags", json={"name": "Quick"}).get_json()

    created = auth_client.post(
        "/api/v1/recipes",
        json={
            "name": "Roast Chicken",
            "servings": 4,
            "totalMinutes": 90,
            "categoryIds": [cat["id"]],
            "tagIds": [tag["id"]],
            "ingredients": [
                {"display": "1 whole chicken", "quantity": 1},
                {"display": "2 tbsp olive oil", "quantity": 2},
            ],
            "steps": [
                {"text": "Preheat oven to 200C."},
                {"text": "Roast for 80 minutes."},
            ],
        },
    ).get_json()
    assert created["slug"] == "roast-chicken"
    assert len(created["ingredients"]) == 2
    assert len(created["steps"]) == 2
    assert created["categories"][0]["name"] == "Dinner"
    assert created["tags"][0]["name"] == "Quick"

    # Fetch by slug as well as id.
    by_slug = auth_client.get("/api/v1/recipes/roast-chicken").get_json()
    assert by_slug["id"] == created["id"]

    updated = auth_client.put(
        f"/api/v1/recipes/{created['id']}",
        json={"name": "Roast Chicken", "rating": 5, "isFavorite": True},
    ).get_json()
    assert updated["rating"] == 5
    assert updated["isFavorite"] is True

    listing = auth_client.get("/api/v1/recipes").get_json()
    assert listing["total"] == 1

    fav = auth_client.get("/api/v1/recipes?favorites=1").get_json()
    assert fav["total"] == 1

    assert (
        auth_client.delete(f"/api/v1/recipes/{created['id']}").status_code == 204
    )
    assert auth_client.get("/api/v1/recipes").get_json()["total"] == 0


def test_unique_slug_on_duplicate_name(auth_client):
    a = auth_client.post("/api/v1/recipes", json={"name": "Soup"}).get_json()
    b = auth_client.post("/api/v1/recipes", json={"name": "Soup"}).get_json()
    assert a["slug"] == "soup"
    assert b["slug"] == "soup-2"


def test_search(auth_client):
    auth_client.post("/api/v1/recipes", json={"name": "Banana Bread"})
    auth_client.post("/api/v1/foods", json={"name": "Banana"})
    res = auth_client.get("/api/v1/search?q=banana").get_json()["results"]
    types = {r["type"] for r in res}
    assert "recipe" in types and "food" in types


def test_group_isolation(app):
    """A recipe created by one group is invisible to another group's user."""
    c = app.test_client()
    c.post(
        "/api/v1/users/register",
        json={"email": "u1@x.com", "password": "pw12345", "name": "U1"},
    )
    t1 = c.post(
        "/api/v1/users/login", json={"username": "u1@x.com", "password": "pw12345"}
    ).get_json()["token"]
    rid = c.post(
        "/api/v1/recipes", json={"name": "Secret"}, headers={"Authorization": t1}
    ).get_json()["id"]

    c.post(
        "/api/v1/users/register",
        json={"email": "u2@x.com", "password": "pw12345", "name": "U2"},
    )
    t2 = c.post(
        "/api/v1/users/login", json={"username": "u2@x.com", "password": "pw12345"}
    ).get_json()["token"]
    assert (
        c.get(f"/api/v1/recipes/{rid}", headers={"Authorization": t2}).status_code
        == 404
    )
    assert (
        c.get("/api/v1/recipes", headers={"Authorization": t2}).get_json()["total"]
        == 0
    )


def test_noauth_mode(noauth_app):
    """With DISABLE_AUTH, requests bind to a default user with no token."""
    c = noauth_app.test_client()
    assert c.get("/api/v1/recipes").status_code == 200
    r = c.post("/api/v1/recipes", json={"name": "Toast"})
    assert r.status_code == 201


def test_api_token_auth(auth_client):
    """A generated API key authenticates like a bearer token."""
    raw = auth_client.post("/api/v1/tokens", json={"name": "HA"}).get_json()["token"]
    assert raw.startswith("mm_")
    fresh = auth_client.application.test_client()
    r = fresh.get("/api/v1/recipes", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
