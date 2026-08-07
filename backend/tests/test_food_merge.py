"""Deleting and merging Food rows.

Two things this covers:

* ``DELETE /foods/<id>`` raised sqlite3.IntegrityError ("FOREIGN KEY constraint
  failed") — a 500 — for any food a recipe used, because the app enables FK
  enforcement and nothing detached the referencing rows first. Only a food
  nothing referenced could be deleted.
* Consolidating duplicates needs a merge that MOVES the references rather than
  dropping them, and it is destructive, so it previews before it acts.
"""
import pytest

from app.extensions import db
from app.models import Food, RecipeIngredient


def _as(client, email):
    """Switch the client to a second household. Matches the token-swap idiom
    already used by test_recipe_components / test_share."""
    client.post("/api/v1/users/register",
                json={"email": email, "password": "password", "name": email})
    token = client.post("/api/v1/users/login",
                        json={"username": email, "password": "password"}).get_json()["token"]
    client.environ_base["HTTP_AUTHORIZATION"] = token
    return client


def _food(client, name, **kw):
    return client.post("/api/v1/foods", json={"name": name, **kw}).get_json()


def _recipe_using(client, name, food_id, display="thing"):
    return client.post("/api/v1/recipes", json={
        "name": name,
        "ingredients": [{"display": display, "foodId": food_id}],
    }).get_json()


# --- delete -----------------------------------------------------------------

def test_deleting_a_food_a_recipe_uses_no_longer_500s(auth_client):
    food = _food(auth_client, "probefood")
    _recipe_using(auth_client, "P", food["id"])

    assert auth_client.delete(f"/api/v1/foods/{food['id']}").status_code == 204


def test_deleting_a_food_keeps_the_recipe_line(auth_client, app):
    """The line is what the user wrote; deleting the food must not delete it."""
    food = _food(auth_client, "probefood")
    recipe = _recipe_using(auth_client, "P", food["id"], display="2 cups probefood")

    auth_client.delete(f"/api/v1/foods/{food['id']}")

    body = auth_client.get(f"/api/v1/recipes/{recipe['id']}").get_json()
    assert [i["display"] for i in body["ingredients"]] == ["2 cups probefood"]
    assert body["ingredients"][0]["food"] is None
    with app.app_context():
        assert db.session.get(Food, food["id"]) is None


def test_deleting_an_unused_food_still_works(auth_client):
    food = _food(auth_client, "lonely")
    assert auth_client.delete(f"/api/v1/foods/{food['id']}").status_code == 204


def test_a_food_from_another_group_is_not_deletable(auth_client):
    """Tenant scoping: the first attack on any id-addressed route."""
    mine = _food(auth_client, "mine")
    assert _as(auth_client, "b@b.com").delete(
        f"/api/v1/foods/{mine['id']}").status_code == 404


# --- merge ------------------------------------------------------------------

def test_merge_previews_before_it_acts(auth_client, app):
    keep = _food(auth_client, "cinnamon")
    drop = _food(auth_client, "Vietnamese cinnamon")
    _recipe_using(auth_client, "Buns", drop["id"])

    body = auth_client.post(f"/api/v1/foods/{keep['id']}/merge",
                            json={"fromId": drop["id"]}).get_json()

    assert body["confirmed"] is False
    # The preview names exactly what is lost and what moves.
    assert body["from"]["name"] == "Vietnamese cinnamon"
    assert body["into"]["name"] == "cinnamon"
    assert body["ingredientCount"] == 1
    assert body["recipes"] == ["Buns"]
    with app.app_context():
        assert db.session.get(Food, drop["id"]) is not None, "preview deleted it"


def test_merge_moves_the_references_and_removes_the_duplicate(auth_client, app):
    keep = _food(auth_client, "cinnamon")
    drop = _food(auth_client, "Vietnamese cinnamon")
    _recipe_using(auth_client, "Buns", drop["id"], display="2 tsp Vietnamese cinnamon")

    body = auth_client.post(f"/api/v1/foods/{keep['id']}/merge",
                            json={"fromId": drop["id"], "confirm": True}).get_json()

    assert body["confirmed"] is True and body["ingredientCount"] == 1
    with app.app_context():
        assert db.session.get(Food, drop["id"]) is None
        rows = db.session.query(RecipeIngredient).filter_by(food_id=keep["id"]).all()
        assert len(rows) == 1
        # The line the user wrote is untouched — only which Food it points at.
        assert rows[0].display == "2 tsp Vietnamese cinnamon"


def test_merge_carries_the_old_name_across_as_an_alias(auth_client):
    """Otherwise the next import recreates the row that was just merged away."""
    keep = _food(auth_client, "cinnamon")
    drop = _food(auth_client, "Vietnamese cinnamon", aliases=["saigon cinnamon"])

    auth_client.post(f"/api/v1/foods/{keep['id']}/merge",
                     json={"fromId": drop["id"], "confirm": True})

    body = auth_client.get("/api/v1/foods").get_json()
    kept = [f for f in body if f["id"] == keep["id"]][0]
    aliases = {a.lower() for a in kept["aliases"]}
    assert "vietnamese cinnamon" in aliases
    assert "saigon cinnamon" in aliases, "the merged food's own aliases were lost"


def test_merging_a_food_into_itself_is_refused(auth_client):
    food = _food(auth_client, "cinnamon")
    r = auth_client.post(f"/api/v1/foods/{food['id']}/merge",
                         json={"fromId": food["id"], "confirm": True})
    assert r.status_code == 400
    assert auth_client.get("/api/v1/foods").get_json(), "the food was deleted"


def test_merging_across_groups_is_refused(auth_client):
    """A merge takes TWO ids, so it needs the scope check on both — checking
    only the URL's id would let another household's food be deleted."""
    theirs = _food(_as(auth_client, "b@b.com"), "theirs")
    mine = _food(_as(auth_client, "a@a.com"), "cinnamon")

    r = auth_client.post(f"/api/v1/foods/{mine['id']}/merge",
                         json={"fromId": theirs["id"], "confirm": True})

    assert r.status_code == 404


def test_merge_needs_a_from_id(auth_client):
    food = _food(auth_client, "cinnamon")
    r = auth_client.post(f"/api/v1/foods/{food['id']}/merge", json={})
    assert r.status_code == 400


# --- create dedupe ----------------------------------------------------------

def test_creating_a_food_that_already_exists_returns_the_existing_one(auth_client):
    """create_food bypassed find-or-create entirely, so the Foods screen could
    manufacture the duplicates the rest of the system works to avoid."""
    first = _food(auth_client, "cinnamon")
    again = auth_client.post("/api/v1/foods", json={"name": "CINNAMON"})

    assert again.status_code == 200, "a duplicate was created instead of reused"
    assert again.get_json()["id"] == first["id"]
    assert len(auth_client.get("/api/v1/foods").get_json()) == 1


def test_creating_a_food_matching_an_alias_returns_the_existing_one(auth_client):
    first = _food(auth_client, "coriander", aliases=["cilantro"])
    again = auth_client.post("/api/v1/foods", json={"name": "Cilantro"})

    assert again.get_json()["id"] == first["id"]
    assert len(auth_client.get("/api/v1/foods").get_json()) == 1


def test_a_genuinely_new_food_is_still_created(auth_client):
    _food(auth_client, "cinnamon")
    r = auth_client.post("/api/v1/foods", json={"name": "nutmeg"})
    assert r.status_code == 201
    assert len(auth_client.get("/api/v1/foods").get_json()) == 2


# --- the second foreign key -------------------------------------------------

def _on_a_list(client, food_id, name="L"):
    """A shopping list carrying a real food_id.

    Note the route: POST /shopping-lists/<id>/items IGNORES foodId, so building
    the fixture that way produced items with food_id NULL and three tests that
    passed with the bug fully present. Only the from-recipes path populates it.
    """
    recipe = client.post("/api/v1/recipes", json={
        "name": f"R{name}",
        "ingredients": [{"display": "thing", "foodId": food_id}],
    }).get_json()
    sl = client.post("/api/v1/shopping-lists", json={"name": name}).get_json()
    client.post(f"/api/v1/shopping-lists/{sl['id']}/from-recipes",
                json={"recipeIds": [recipe["id"]]})
    items = client.get(f"/api/v1/shopping-lists/{sl['id']}").get_json()["items"]
    assert any(i.get("foodId") == food_id for i in items), \
        "fixture is vacuous: no shopping item actually references the food"
    return sl


def test_deleting_a_food_on_a_shopping_list_no_longer_500s(auth_client):
    """recipe_ingredients is not the only table referencing foods.id —
    shopping_list_items does too, and detaching only the first still left the
    delete violating a foreign key."""
    food = _food(auth_client, "probefood")
    _on_a_list(auth_client, food["id"])

    assert auth_client.delete(f"/api/v1/foods/{food['id']}").status_code == 204


def test_deleting_a_food_keeps_the_shopping_line(auth_client):
    food = _food(auth_client, "probefood")
    sl = _on_a_list(auth_client, food["id"])

    auth_client.delete(f"/api/v1/foods/{food['id']}")

    items = auth_client.get(f"/api/v1/shopping-lists/{sl['id']}").get_json()["items"]
    assert len(items) == 1 and items[0]["foodId"] is None


def test_merge_moves_shopping_references_too(auth_client, app):
    """A merge that moves only the recipe references leaves the shopping list
    pointing at a row that is about to be deleted."""
    keep = _food(auth_client, "cinnamon")
    drop = _food(auth_client, "Vietnamese cinnamon")
    sl = _on_a_list(auth_client, drop["id"])

    r = auth_client.post(f"/api/v1/foods/{keep['id']}/merge",
                         json={"fromId": drop["id"], "confirm": True})

    assert r.status_code == 200
    items = auth_client.get(f"/api/v1/shopping-lists/{sl['id']}").get_json()["items"]
    assert items[0]["foodId"] == keep["id"]
    with app.app_context():
        assert db.session.get(Food, drop["id"]) is None


# --- hardening (found by adversarial review) --------------------------------

def test_a_comma_in_a_merged_name_cannot_forge_extra_aliases(auth_client):
    """aliases is a CSV string, so appending a name containing a comma splits
    into two aliases. Merging "salt, kosher" into cinnamon gave cinnamon the
    aliases ['salt', 'kosher'], after which POST /foods {"name":"salt"} returned
    the cinnamon row — and every DB-backed resolver inherited the same wrong
    mapping.
    """
    keep = _food(auth_client, "cinnamon")
    drop = _food(auth_client, "salt, kosher")

    auth_client.post(f"/api/v1/foods/{keep['id']}/merge",
                     json={"fromId": drop["id"], "confirm": True})

    kept = auth_client.get("/api/v1/foods").get_json()[0]
    assert "salt" not in {a.lower() for a in kept["aliases"]}, \
        f"a delimiter forged an alias: {kept['aliases']}"
    # And the downstream consequence is gone.
    again = auth_client.post("/api/v1/foods", json={"name": "salt"})
    assert again.get_json()["name"] != "cinnamon"


def test_repeated_merges_do_not_overflow_the_alias_column(auth_client, app):
    """Food.aliases is String(512). SQLite ignores that; Postgres raises
    "value too long", and once over the limit even a plain PUT on the row
    fails. Merge only ever appended.
    """
    keep = _food(auth_client, "base")
    for i in range(40):
        drop = _food(auth_client, f"a-fairly-long-food-name-number-{i:02d}")
        auth_client.post(f"/api/v1/foods/{keep['id']}/merge",
                         json={"fromId": drop["id"], "confirm": True})

    with app.app_context():
        stored = db.session.get(Food, keep["id"]).aliases or ""
    assert len(stored) <= 512, f"aliases grew to {len(stored)} chars"
    # Still usable afterwards.
    assert auth_client.put(f"/api/v1/foods/{keep['id']}",
                           json={"aisle": "Baking"}).status_code == 200


@pytest.mark.parametrize("bad", [123, {"a": 1}, ["x"], True])
def test_a_non_string_from_id_is_a_400_not_a_500(auth_client, bad):
    """Untrusted boundary: .strip() on a non-string raised AttributeError."""
    food = _food(auth_client, "cinnamon")
    r = auth_client.post(f"/api/v1/foods/{food['id']}/merge", json={"fromId": bad})
    assert r.status_code == 400


def test_reusing_an_existing_food_says_so(auth_client):
    """The 200-vs-201 distinction is invisible to a client that only reads the
    body, and the supplied aisle/description are NOT applied to the existing
    row — a create must not silently mutate something that already exists.
    """
    first = _food(auth_client, "cinnamon", aisle="Spices")
    body = auth_client.post("/api/v1/foods",
                            json={"name": "cinnamon", "aisle": "Baking"}).get_json()

    assert body["id"] == first["id"]
    assert body["reused"] is True
    assert body["aisle"] == "Spices", "a create silently overwrote an existing row"


def test_a_created_food_is_not_marked_reused(auth_client):
    assert auth_client.post("/api/v1/foods",
                            json={"name": "nutmeg"}).get_json()["reused"] is False


def test_the_merge_preview_does_not_issue_a_query_per_recipe(auth_client, app):
    """N+1: the preview read line.recipe.name off lazily-loaded rows."""
    from sqlalchemy import event

    keep = _food(auth_client, "cinnamon")
    drop = _food(auth_client, "Vietnamese cinnamon")
    for i in range(6):
        _recipe_using(auth_client, f"R{i}", drop["id"])

    seen = []
    with app.app_context():
        engine = db.engine

    def record(conn, cursor, statement, params, context, executemany):
        if "FROM recipes" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        body = auth_client.post(f"/api/v1/foods/{keep['id']}/merge",
                                json={"fromId": drop["id"]}).get_json()
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(body["recipes"]) == 6
    assert len(seen) <= 2, f"{len(seen)} recipe queries for 6 lines (N+1)"
