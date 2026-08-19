"""Structured-ingredient support for the builder: the deterministic paste parser
and the non-destructive weight annotation on the recipe view."""


def test_parse_lines_splits_qty_unit_food(auth_client):
    res = auth_client.post("/api/v1/recipes/parse", json={
        "lines": ["2 cups flour", "1 tsp salt", "3 eggs"]})
    rows = res.get_json()["ingredients"]
    assert [r["quantity"] for r in rows] == [2, 1, 3]
    assert rows[0]["unit"] == "cup" and rows[0]["food"] == "flour"
    assert rows[1]["unit"] == "tsp"
    assert rows[2]["unit"] == "" and rows[2]["food"] == "eggs"  # no unit


def test_parse_lines_accepts_a_single_string_blob(auth_client):
    res = auth_client.post("/api/v1/recipes/parse", json={"lines": "1 cup milk\n2 tbsp sugar"})
    rows = res.get_json()["ingredients"]
    assert len(rows) == 2 and rows[0]["food"] == "milk"


def test_parse_lines_requires_lines(auth_client):
    assert auth_client.post("/api/v1/recipes/parse", json={"lines": 5}).status_code == 422


def test_parse_lines_handles_bulleted_paste(auth_client):
    """A pasted blog list is usually bulleted, and the quantity match is anchored
    at the start of the line — so these used to parse to quantity 0 with the
    marker itself stuck in the food name."""
    res = auth_client.post("/api/v1/recipes/parse", json={
        "lines": ["- 2 cups flour", "• 1 tsp salt", "1. 3 eggs"]})
    rows = res.get_json()["ingredients"]

    assert [r["quantity"] for r in rows] == [2, 1, 3]
    assert [r["food"] for r in rows] == ["flour", "salt", "eggs"]


def test_parse_lines_surfaces_a_dropped_range_in_the_note(auth_client):
    """Only the low end can be the structured quantity, but the high end must
    not vanish without trace."""
    res = auth_client.post("/api/v1/recipes/parse", json={
        "lines": ["2-3 cloves garlic", "2 cups flour"]})
    rows = res.get_json()["ingredients"]

    assert rows[0]["quantity"] == 2
    assert rows[0]["note"] == "or up to 3"
    assert rows[1]["note"] == ""      # no range → no note invented


def test_parse_lines_keeps_the_original_line_for_review(auth_client):
    """display is the user's ground truth for spotting a bad parse."""
    res = auth_client.post("/api/v1/recipes/parse", json={"lines": ["- 2 cups flour"]})

    assert res.get_json()["ingredients"][0]["display"] == "- 2 cups flour"


def test_weight_view_annotates_in_parens_without_mutating(auth_client):
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Bread", "servings": 2,
        "ingredients": [{"display": "1 cup flour"}, {"display": "3 eggs"}],
    }).get_json()["id"]

    weighted = auth_client.get(f"/api/v1/recipes/{rid}?units=weight").get_json()
    disp = [i["display"] for i in weighted["ingredients"]]
    assert disp[0].startswith("1 cup flour") and disp[0].endswith(")")  # annotated
    assert disp[1] == "3 eggs"  # not convertible → unchanged

    # The stored recipe is untouched (annotation is view-only).
    plain = auth_client.get(f"/api/v1/recipes/{rid}").get_json()
    assert plain["ingredients"][0]["display"] == "1 cup flour"


def test_structured_ingredient_round_trips(auth_client):
    """A row saved with quantity/unit/food/note comes back with the same
    structured fields (the Mealie-style schema is functional end to end)."""
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Roundtrip",
        "ingredients": [
            {"display": "2 cup flour, sifted", "quantity": 2, "unit": "cup",
             "food": "flour", "note": "sifted"},
            {"display": "3 eggs, beaten", "quantity": 3, "unit": "",
             "food": "eggs", "note": "beaten"},
        ],
    }).get_json()["id"]

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"]
    assert got[0]["quantity"] == 2
    assert got[0]["unit"]["name"] == "cup"
    assert got[0]["food"]["name"] == "flour"
    assert got[0]["note"] == "sifted"
    # No unit is fine; food + note still structured.
    assert got[1]["unit"] is None
    # "eggs" canonicalises to the singular seed food — one row per ingredient
    # is the point, so "egg" and "eggs" must not be two Foods.
    assert got[1]["food"]["name"] == "egg" and got[1]["note"] == "beaten"


def test_food_reused_not_duplicated_across_recipes(auth_client):
    """Saving the same food name twice reuses one Food row (autocomplete source
    stays clean)."""
    for _ in range(2):
        auth_client.post("/api/v1/recipes", json={
            "name": "R", "ingredients": [{"display": "1 onion", "food": "onion"}]})
    foods = [f["name"] for f in auth_client.get("/api/v1/foods").get_json()]
    assert foods.count("onion") == 1


def test_ingredient_exposes_a_lossless_amount_split(auth_client):
    """The read view shows the amount as its own scannable column. It must never
    be built by re-rendering food.name: names are canonicalized on save, so
    "granulated sugar" becomes the "sugar" Food and the reader would see less
    than they typed. restText is `display` minus its leading amount instead."""
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Split", "servings": 2,
        "ingredients": [
            {"display": "2/3 cup granulated sugar", "quantity": 0.6667,
             "unit": "cup", "food": "granulated sugar"},
            {"display": "a good knob of butter"},          # no amount at all
        ],
    }).get_json()["id"]

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"]

    assert got[0]["amountText"] == "2/3"          # not the raw 0.6667 float
    assert got[0]["unitText"] == "cup"
    assert got[0]["restText"] == "granulated sugar"
    assert got[0]["food"]["name"] == "sugar"      # the lossy part we must not use
    # An unstructured line keeps its whole text and simply has no amount.
    assert got[1]["amountText"] == "" and got[1]["restText"] == "a good knob of butter"


def test_scaled_view_keeps_the_amount_split_consistent(auth_client):
    """A stale amountText beside a scaled display would show two different
    numbers for one ingredient, and a raw float would leak on a fractional
    factor."""
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Split2", "servings": 2,
        "ingredients": [
            {"display": "1 cup milk", "quantity": 1, "unit": "cup", "food": "milk"},
            {"display": "Salt to taste"},
        ],
    }).get_json()["id"]

    got = auth_client.get(f"/api/v1/recipes/{rid}?servings=3").get_json()["ingredients"]

    assert got[0]["amountText"] == "1 1/2"        # 1.5, rendered not raw
    assert got[0]["unitText"] == "cups"           # agrees with the scaled amount
    assert got[0]["restText"] == "milk"
    assert got[0]["scaled"] is True
    # The line with no parseable amount is flagged, so the reader can tell it
    # apart from the scaled ones instead of trusting it silently.
    assert got[1]["scaled"] is False
