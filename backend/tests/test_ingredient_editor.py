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
