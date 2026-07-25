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


def test_shopping_scales_component_by_its_quantity(auth_client):
    sauce = _recipe(auth_client, "Sauce2", ["4 cloves garlic"])
    dish = auth_client.post("/api/v1/recipes", json={
        "name": "BigMain",
        "ingredients": [
            {"display": "2 batches Sauce2", "quantity": 2, "unit": "batch",
             "refRecipeId": sauce["id"]},
        ],
    }).get_json()
    sl = auth_client.post("/api/v1/shopping-lists", json={"name": "L2"}).get_json()
    res = auth_client.post(f"/api/v1/shopping-lists/{sl['id']}/from-recipes",
                           json={"recipeIds": [dish["id"]]}).get_json()
    garlic = next(i for i in res["items"] if "garlic" in i["display"])
    # 4 cloves × 2 batches = 8 (the component quantity scales the expansion).
    assert garlic["quantity"] == 8


def test_deleting_a_referenced_recipe_nulls_the_component(auth_client):
    sauce = _recipe(auth_client, "Doomed Sauce", ["salt"])
    dish = auth_client.post("/api/v1/recipes", json={
        "name": "Host", "ingredients": [
            {"display": "1 batch Doomed Sauce", "quantity": 1, "refRecipeId": sauce["id"]}]}).get_json()
    assert auth_client.delete(f"/api/v1/recipes/{sauce['id']}").status_code == 204
    # The component ref is gone (no dangling reference); the row survives as text.
    ings = auth_client.get(f"/api/v1/recipes/{dish['id']}").get_json()["ingredients"]
    assert ings[0]["refRecipe"] is None
    assert "Doomed Sauce" in ings[0]["display"]


def test_shopping_expansion_is_depth_bounded(auth_client):
    # A deep chain must terminate (no hang): build a 10-deep linked chain and
    # confirm from-recipes returns without exploding.
    from app.services.shopping import _MAX_COMPONENT_DEPTH
    prev = None
    for i in range(10):
        ings = [{"display": f"item{i}", "food": f"item{i}"}]
        if prev:
            ings.append({"display": f"1 batch r{i - 1}", "quantity": 1, "refRecipeId": prev})
        prev = auth_client.post("/api/v1/recipes", json={"name": f"r{i}", "ingredients": ings}).get_json()["id"]
    sl = auth_client.post("/api/v1/shopping-lists", json={"name": "deep"}).get_json()
    res = auth_client.post(f"/api/v1/shopping-lists/{sl['id']}/from-recipes",
                           json={"recipeIds": [prev]}).get_json()
    # Expansion stops at the depth cap, so only the top ~depth levels' items
    # appear as foods; the rest remain as component lines. It returns, bounded.
    assert res["added"] >= 1
    assert _MAX_COMPONENT_DEPTH == 4


def test_shopping_display_not_doubled_for_unstructured_lines(auth_client):
    # A display-only ingredient ("6 bone-in chicken thighs", no structured food)
    # must not render its quantity twice on the list: the item stores qty+unit
    # separately and the name is stripped of the leading qty/unit.
    dish = auth_client.post("/api/v1/recipes", json={
        "name": "Roast", "ingredients": [
            {"display": "6 bone-in chicken thighs"},
            {"display": "2 tbsp olive oil"},
        ]}).get_json()
    sl = auth_client.post("/api/v1/shopping-lists", json={"name": "R"}).get_json()
    res = auth_client.post(f"/api/v1/shopping-lists/{sl['id']}/from-recipes",
                           json={"recipeIds": [dish["id"]]}).get_json()
    by = {i["display"]: i for i in res["items"]}
    assert "bone-in chicken thighs" in by            # name only, no leading "6"
    assert by["bone-in chicken thighs"]["quantity"] == 6
    assert "olive oil" in by and by["olive oil"]["unit"] == "tbsp"
    assert by["olive oil"]["quantity"] == 2
