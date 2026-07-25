"""Linking a recipe as a component: the ref round-trips, is group-scoped, and
its ingredients expand into the shopping list (features from this session)."""


def _recipe(client, name, ingredients):
    return client.post("/api/v1/recipes", json={
        "name": name,
        "ingredients": [{"display": d, "food": d} for d in ingredients],
    }).get_json()


def test_component_ref_round_trips(auth_client):
    sauce = _recipe(auth_client, "Garlic Confit", ["garlic", "olive oil"])
    dish = auth_client.post("/api/v1/recipes", json={
        "name": "Pasta",
        "ingredients": [
            {"display": "500 g pasta", "quantity": 500, "unit": "g", "food": "pasta"},
            {"display": "1 batch Garlic Confit", "quantity": 1, "unit": "batch",
             "refRecipeId": sauce["id"]},
        ],
    }).get_json()

    ings = auth_client.get(f"/api/v1/recipes/{dish['id']}").get_json()["ingredients"]
    comp = [i for i in ings if i["refRecipe"]]
    assert len(comp) == 1
    assert comp[0]["refRecipe"]["id"] == sauce["id"]
    assert comp[0]["refRecipe"]["name"] == "Garlic Confit"
    assert comp[0]["food"] is None  # a component references a recipe, not a food


def test_component_ref_to_other_group_is_ignored(client):
    def tok(email):
        client.post("/api/v1/users/register",
                    json={"email": email, "password": "password", "name": email})
        return client.post("/api/v1/users/login",
                           json={"username": email, "password": "password"}).get_json()["token"]

    client.environ_base["HTTP_AUTHORIZATION"] = tok("a@a.com")
    mine = _recipe(client, "Mine", ["x"])["id"]
    client.environ_base["HTTP_AUTHORIZATION"] = tok("b@b.com")
    dish = client.post("/api/v1/recipes", json={
        "name": "Theirs", "ingredients": [
            {"display": "1 batch Mine", "refRecipeId": mine}]}).get_json()
    ings = client.get(f"/api/v1/recipes/{dish['id']}").get_json()["ingredients"]
    assert ings[0]["refRecipe"] is None  # cross-group ref not linked


def test_shopping_expands_component_recipe(auth_client):
    sauce = _recipe(auth_client, "Sauce", ["8 cloves garlic", "1 cup olive oil"])
    dish = auth_client.post("/api/v1/recipes", json={
        "name": "Main",
        "ingredients": [
            {"display": "flour", "food": "flour"},
            {"display": "1 batch Sauce", "quantity": 1, "unit": "batch",
             "refRecipeId": sauce["id"]},
        ],
    }).get_json()

    sl = auth_client.post("/api/v1/shopping-lists", json={"name": "L"}).get_json()
    res = auth_client.post(f"/api/v1/shopping-lists/{sl['id']}/from-recipes",
                           json={"recipeIds": [dish["id"]]}).get_json()
    names = {i["display"] for i in res["items"]}
    # The component expanded into the sauce's ingredients (not a "Sauce" line).
    assert "flour" in names
    assert "8 cloves garlic" in names and "1 cup olive oil" in names
    assert "Sauce" not in names and "1 batch Sauce" not in names
