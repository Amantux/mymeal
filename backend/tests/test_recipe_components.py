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


# --- suggesting a link for a line that is already a recipe -----------------

def _suggest(client, texts, exclude=None):
    body = {"texts": texts}
    if exclude:
        body["excludeId"] = exclude
    return client.post("/api/v1/recipes/component-suggestions",
                       json=body).get_json()["matches"]


def test_an_ingredient_naming_a_recipe_is_suggested_as_a_component(auth_client):
    """The whole point: the user's soup lists its stock as a plain line, and
    nothing noticed that a recipe by that exact name already existed."""
    stock = _recipe(auth_client, "Roasted Oxtail Stock with Browned Vegetables",
                    ["oxtail", "onion"])

    matches = _suggest(auth_client,
                       ["Roasted Oxtail Stock with Browned Vegetables", "2 onions"])

    assert matches["Roasted Oxtail Stock with Browned Vegetables"]["id"] == stock["id"]
    # And the ordinary line is left alone. A hint on every row is noise, and
    # noise on a row the user did not ask about costs attention every render.
    assert "2 onions" not in matches


def test_a_line_matching_nothing_gets_no_suggestion(auth_client):
    _recipe(auth_client, "Garlic Confit", ["garlic"])

    assert _suggest(auth_client, ["500 g pasta", "a pinch of salt"]) == {}


def test_a_recipe_is_never_suggested_as_its_own_component(auth_client):
    """A self-reference is the one component the API already refuses to store,
    so suggesting it would be an offer that cannot be accepted."""
    soup = _recipe(auth_client, "French Onion Soup", ["onion"])

    assert _suggest(auth_client, ["French Onion Soup"], exclude=soup["id"]) == {}
    # Without the exclusion it IS a confident match — proving the filter is
    # what suppressed it, not a failure to match.
    assert "French Onion Soup" in _suggest(auth_client, ["French Onion Soup"])


def test_only_confident_matches_are_returned(auth_client):
    """Two recipes sharing a word must not produce a hint.

    `recipe_resolve` already decides this; the test pins that this endpoint
    DEFERS to it rather than applying a looser rule of its own, because a
    second confidence policy is the drift the shared resolver prevents.
    """
    _recipe(auth_client, "Chicken Stock", ["chicken"])
    _recipe(auth_client, "Beef Stock", ["beef"])

    assert _suggest(auth_client, ["stock"]) == {}


def test_a_description_mention_does_not_trigger_a_suggestion(auth_client):
    """Asks "is this line the NAME of a recipe", not "does a recipe mention
    this". A stock whose description talks about onions must not be offered
    for the line "2 onions"."""
    auth_client.post("/api/v1/recipes", json={
        "name": "Oxtail Stock", "description": "Built on onions and carrots.",
        "ingredients": [{"display": "oxtail", "food": "oxtail"}]})

    assert _suggest(auth_client, ["onions", "carrots"]) == {}


def test_a_non_list_texts_payload_is_rejected(auth_client):
    assert auth_client.post("/api/v1/recipes/component-suggestions",
                            json={"texts": "flour"}).status_code == 400


def test_the_text_count_is_capped(auth_client):
    """The body is caller-supplied; a 400-line paste must not turn one click
    into an unbounded scoring loop."""
    _recipe(auth_client, "Garlic Confit", ["garlic"])

    matches = _suggest(auth_client, [f"line {n}" for n in range(200)]
                       + ["Garlic Confit"])

    # The real name sits past the cap, so it is not scored — which is the cap
    # working. Asserting the absence rather than a count, because the count is
    # the implementation's and the behaviour is "we stopped".
    assert matches == {}


# --- a component's quantity is a BATCH count, and the unit decides ---------

def _shopping_qty(client, recipe_id, food_name):
    """Grams/amount the shopping list wants for `food_name`, via expansion."""
    r = client.post("/api/v1/shopping-lists", json={
        "name": "L", "recipeIds": [recipe_id]})
    assert r.status_code in (200, 201), r.get_json()
    items = client.get(
        f"/api/v1/shopping-lists/{r.get_json()['id']}").get_json()["items"]
    for it in items:
        if food_name.lower() in (it.get("name") or "").lower():
            return it.get("quantity")
    return None


def test_one_unit_of_a_component_means_one_batch(auth_client):
    """The user's own phrasing. "unit" counts whole makings of the thing, so
    it must behave exactly like "batch" and not fall back to anything."""
    stock = _recipe(auth_client, "Oxtail Stock", ["oxtail"])
    soup = auth_client.post("/api/v1/recipes", json={
        "name": "Onion Soup", "ingredients": [
            {"display": "1 unit Oxtail Stock", "quantity": 1, "unit": "unit",
             "refRecipeId": stock["id"]}]}).get_json()

    ings = auth_client.get(f"/api/v1/recipes/{soup['id']}").get_json()["ingredients"]

    assert ings[0]["refRecipe"]["name"] == "Oxtail Stock"
    # Not flagged: "unit" is a whole-batch word, so the amount is readable.
    assert "componentAmountUnclear" not in ings[0]


def test_a_volume_amount_on_a_component_is_flagged_not_silently_multiplied(auth_client):
    """⚠️ The latent bug this guard exists for.

    `_component_mult` read the quantity as a batch count and ignored the unit,
    so "2 cups oxtail stock" expanded to TWO FULL BATCHES of stock in every
    shopping list, nutrition total and can-I-cook check. Unboundedly wrong: the
    error scales with the sub-recipe's yield.

    Reachable through the API today — the editor only ever writes "batch", but
    the AI parser, MCP and the HA integration all post rows with a unit.

    1x is not the TRUE answer (that needs a yield this app does not record) but
    it is bounded and conservative, and the row is flagged so a human can say
    what a guess cannot.
    """
    stock = _recipe(auth_client, "Oxtail Stock", ["oxtail"])
    soup = auth_client.post("/api/v1/recipes", json={
        "name": "Onion Soup", "ingredients": [
            {"display": "2 cups Oxtail Stock", "quantity": 2, "unit": "cup",
             "refRecipeId": stock["id"]}]}).get_json()

    ings = auth_client.get(f"/api/v1/recipes/{soup['id']}").get_json()["ingredients"]

    assert ings[0]["componentAmountUnclear"] is True


def test_the_flag_is_absent_on_rows_it_cannot_describe(auth_client):
    """Absent, not False. A plain ingredient has no component amount to be
    unclear about, and a client must not be able to read "this is fine" into a
    flag about a situation that does not apply."""
    dish = auth_client.post("/api/v1/recipes", json={
        "name": "Toast", "ingredients": [
            {"display": "2 slices bread", "quantity": 2, "unit": "slice",
             "food": "bread"}]}).get_json()

    ings = auth_client.get(f"/api/v1/recipes/{dish['id']}").get_json()["ingredients"]

    assert "componentAmountUnclear" not in ings[0]


def test_expansion_reads_batch_units_as_multipliers_and_others_as_one():
    """The policy directly, without a round trip.

    MUTATION GUARD: delete the `component_unit_is_multiple` check in
    `_component_mult` and the "cup" case returns 2.0 instead of 1.0.
    """
    from app.services.components import _component_mult

    class _Unit:
        def __init__(self, name):
            self.name = name

    class _Ing:
        def __init__(self, quantity, unit):
            self.quantity = quantity
            self.unit = _Unit(unit) if unit else None

    assert _component_mult(_Ing(2, "batch")) == 2.0
    assert _component_mult(_Ing(2, "units")) == 2.0
    assert _component_mult(_Ing(2, None)) == 2.0
    assert _component_mult(_Ing(0, "batch")) == 1.0      # blank means one
    assert _component_mult(_Ing(2, "cup")) == 1.0        # NOT two batches
    assert _component_mult(_Ing(500, "g")) == 1.0        # NOT five hundred


def test_the_reported_scenario_end_to_end(auth_client):
    """The exact case from the report, start to finish.

    A soup whose ingredient line NAMES a stock recipe the user already has,
    spelled identically — and which was unlinkable: the editor could only
    append a component through the picker, so the only route was to delete the
    line and re-add it, losing the amount.
    """
    stock = _recipe(auth_client,
                    "Roasted Oxtail Stock with Pressure Cooker Browned Vegetables",
                    ["oxtail", "onion"])
    soup = auth_client.post("/api/v1/recipes", json={
        "name": "Pressure Cooker French Onion Soup with Sherry and White Wine",
        "ingredients": [
            {"display": "1 unit Roasted Oxtail Stock with Pressure Cooker "
                        "Browned Vegetables", "quantity": 1, "unit": "unit",
             "food": "Roasted Oxtail Stock with Pressure Cooker Browned Vegetables"},
            {"display": "3 onions", "quantity": 3, "food": "onion"},
        ]}).get_json()

    # 1. As imported, the line is plain text — nothing to click.
    before = auth_client.get(f"/api/v1/recipes/{soup['id']}").get_json()["ingredients"]
    assert all(i["refRecipe"] is None for i in before)

    # 2. The app now notices, and offers only the line that is really a recipe.
    matches = _suggest(
        auth_client,
        [(i["food"] or {}).get("name") or i["display"] for i in before],
        exclude=soup["id"])
    assert len(matches) == 1
    assert next(iter(matches.values()))["id"] == stock["id"]

    # 3. Accepting the link keeps the amount and unit the author wrote. This is
    #    what the row's link button does; "1 unit" must not become "1 batch".
    rows = [{"display": i["display"], "quantity": i["quantity"],
             "unit": (i["unit"] or {}).get("name"),
             "food": (i["food"] or {}).get("name") if i["food"] else None}
            for i in before]
    rows[0]["refRecipeId"] = stock["id"]
    rows[0]["food"] = None
    auth_client.put(f"/api/v1/recipes/{soup['id']}",
                    json={"name": soup["name"], "ingredients": rows})

    after = auth_client.get(f"/api/v1/recipes/{soup['id']}").get_json()["ingredients"]
    linked = [i for i in after if i["refRecipe"]]
    assert len(linked) == 1
    assert linked[0]["refRecipe"]["id"] == stock["id"]
    assert linked[0]["quantity"] == 1
    assert (linked[0]["unit"] or {}).get("name") == "unit"
    # "unit" counts whole makings, so the amount is readable and unflagged.
    assert "componentAmountUnclear" not in linked[0]

    # 4. And the link is load-bearing, not decorative: the stock's own
    #    ingredients now expand into the soup's shopping list.
    sl = auth_client.post("/api/v1/shopping-lists", json={"name": "L"}).get_json()
    res = auth_client.post(f"/api/v1/shopping-lists/{sl['id']}/from-recipes",
                           json={"recipeIds": [soup["id"]]}).get_json()
    names = {i["display"] for i in res["items"]}

    assert "oxtail" in names, f"the component did not expand: {sorted(names)}"
    # The component line itself is NOT on the list — you do not buy a stock,
    # you make it from the things the list now contains.
    assert not any("oxtail stock" in n.lower() for n in names), sorted(names)
