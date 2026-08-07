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
    # The names are the CANONICAL foods: this helper passes the whole display
    # line as the food name, so before canonicalisation the shopping list —
    # and the Food table — carried rows literally called "8 cloves garlic".
    assert "flour" in names
    assert "garlic" in names and "olive oil" in names
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


def _recipe_with(client, name, ingredients, **extra):
    payload = {"name": name,
               "ingredients": [{"display": d, "food": d} for d in ingredients]}
    payload.update(extra)
    return client.post("/api/v1/recipes", json=payload).get_json()


def test_effective_nutrition_includes_components(auth_client):
    sauce = _recipe_with(auth_client, "Stock", ["water", "bones"],
                         nutrition={"calories": 100, "protein": 5})
    dish = auth_client.post("/api/v1/recipes", json={
        "name": "Soup", "nutrition": {"calories": 50},
        "ingredients": [
            {"display": "onions", "food": "onions"},
            {"display": "2 batches Stock", "quantity": 2, "refRecipeId": sauce["id"]},
        ]}).get_json()
    out = auth_client.get(f"/api/v1/recipes/{dish['id']}").get_json()
    assert out["nutrition"] == {"calories": 50}          # the recipe's own only
    assert out["effectiveNutrition"]["calories"] == 250  # 50 + 100*2
    assert out["effectiveNutrition"]["protein"] == 10     # 0 + 5*2


def test_effective_nutrition_null_when_none(auth_client):
    plain = _recipe(auth_client, "Toast", ["bread"])
    out = auth_client.get(f"/api/v1/recipes/{plain['id']}").get_json()
    assert out["effectiveNutrition"] is None


def test_meal_plan_demand_expands_components(auth_client, app):
    from types import SimpleNamespace

    from app.models import Recipe
    from app.services.plan_ingredients import flatten_plan

    sauce = _recipe(auth_client, "Confit", ["garlic", "olive oil"])
    dish = auth_client.post("/api/v1/recipes", json={
        "name": "Pasta", "servings": 4, "ingredients": [
            {"display": "pasta", "food": "pasta"},
            {"display": "1 batch Confit", "quantity": 1, "refRecipeId": sauce["id"]},
        ]}).get_json()
    with app.app_context():
        r = __import__("app.extensions", fromlist=["db"]).db.session.get(Recipe, dish["id"])
        entry = SimpleNamespace(recipe=r, servings=None, date=None, meal_type="dinner")
        names = {i["name"] for i in flatten_plan([entry])}
    # the component's own ingredients flow through, not a single "Confit" line
    assert {"garlic", "olive oil", "pasta"} <= names


def test_inventory_matches_component_ingredients(auth_client, app):
    from app.extensions import db
    from app.models import Recipe
    from app.services.inventory import rank_recipes

    sauce = _recipe(auth_client, "Base", ["garlic", "olive oil"])
    dish = auth_client.post("/api/v1/recipes", json={
        "name": "Dish", "ingredients": [
            {"display": "flour", "food": "flour"},
            {"display": "1 batch Base", "quantity": 1, "refRecipeId": sauce["id"]},
        ]}).get_json()
    with app.app_context():
        r = db.session.get(Recipe, dish["id"])
        on_hand = [{"name": "garlic"}, {"name": "olive oil"}, {"name": "flour"}]
        scored = rank_recipes([r], on_hand)
    # 3 leaf ingredients (flour + garlic + olive oil), all covered — not a
    # 2-ingredient recipe with an opaque "Base" component line.
    assert scored[0]["totalCount"] == 3
    assert scored[0]["haveCount"] == 3
    assert scored[0]["coverage"] == 1.0
