"""A recipe ingredient's foodId/unitId must belong to the caller's group.

_set_ingredients validated refRecipeId (group-scoped) and taxonomy, but wrote a
caller-supplied foodId/unitId onto the RecipeIngredient with no scope check — so
group A could reference group B's Food by id and read it back through
food_out (name, description, aliases, aisle). Known-id (UUIDs aren't
enumerable) but still a tenancy-boundary violation.
"""


def _as(client, email):
    client.post("/api/v1/users/register",
                json={"email": email, "password": "password", "name": email})
    tok = client.post("/api/v1/users/login",
                      json={"username": email, "password": "password"}).get_json()["token"]
    client.environ_base["HTTP_AUTHORIZATION"] = tok
    return client


def test_a_recipe_cannot_reference_another_groups_food(client):
    # group B owns a secret food
    _as(client, "b@b.com")
    victim = client.post("/api/v1/foods",
                         json={"name": "SecretTruffle", "description": "victim-only"}
                         ).get_json()

    # group A tries to reference it by id
    _as(client, "a@a.com")
    body = client.post("/api/v1/recipes", json={
        "name": "Heist",
        "ingredients": [{"display": "1 truffle", "foodId": victim["id"]}],
    }).get_json()

    ing = body["ingredients"][0]
    # The cross-group food must NOT be linked; it must never appear in the read-back.
    assert ing.get("food") is None or ing["food"]["name"] != "SecretTruffle"
    # and no cross-group Food leaked into the response
    assert "SecretTruffle" not in _dump(body)


def test_a_recipe_cannot_reference_another_groups_unit(client):
    _as(client, "b2@b.com")
    # a unit is created via the units API
    vunit = client.post("/api/v1/units",
                        json={"name": "victimcup", "abbreviation": "vc"}).get_json()

    _as(client, "a2@a.com")
    body = client.post("/api/v1/recipes", json={
        "name": "Heist2",
        "ingredients": [{"display": "2 x", "quantity": 2, "unitId": vunit["id"]}],
    }).get_json()

    assert "victimcup" not in _dump(body)


def test_a_recipe_can_still_use_its_own_group_food(client):
    _as(client, "c@c.com")
    mine = client.post("/api/v1/foods", json={"name": "MyOnion"}).get_json()
    body = client.post("/api/v1/recipes", json={
        "name": "Soup",
        "ingredients": [{"display": "1 onion", "foodId": mine["id"]}],
    }).get_json()
    assert body["ingredients"][0]["food"]["name"] == "MyOnion"


def _dump(obj):
    import json
    return json.dumps(obj)
