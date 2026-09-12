"""The free-text ingredient lane: a line the author declared to be PROSE.

Every ingredient row's `food` text is find-or-created into the group's shared
Food catalog on save, so prose like "a good knob of butter, for finishing" used
to mint a Food row that then appeared in every ingredient autocomplete in the
app. A row may now carry `freeText: true`, which skips food/unit resolution
entirely and stores the line as display-only.

The flag is EXPLICIT, never inferred from an empty food: emptiness already means
"an importer could not structure this line", and the editor deliberately
re-parses those. Conflating the two is what would destroy the author's choice on
the next save, so `test_an_empty_food_alone_is_not_treated_as_free_text` is the
mutation guard for that and must fail if the flag is ever derived.
"""


def _foods(client):
    return [f["name"] for f in client.get("/api/v1/foods").get_json()]


def _units(client):
    return [u["name"] for u in client.get("/api/v1/units").get_json()]


def test_free_text_row_mints_no_food(auth_client):
    """The prose is sent in `food`, which is the shape that actually pollutes:
    the editor drops a whole legacy line into that field, and find-or-create
    turns the sentence into a catalog row.

    Sending only `display` would make this test vacuous — with no `food` key
    nothing is minted with or without the flag, so it would pass against the
    unguarded code. (Confirmed by mutation: removing the guard left the
    display-only version green.)"""
    auth_client.post("/api/v1/recipes", json={
        "name": "Prose",
        "ingredients": [{"display": "a good knob of butter, for finishing",
                         "food": "a good knob of butter, for finishing",
                         "freeText": True}],
    })

    assert _foods(auth_client) == []


def test_free_text_row_mints_no_unit(auth_client):
    """The display parse is a second catalog writer: it resolves a unit out of
    the free-text line ("2 handfuls of rocket" -> handful) and find-or-creates
    THAT. Skipping only the food leaves half the pollution behind."""
    auth_client.post("/api/v1/recipes", json={
        "name": "Prose",
        "ingredients": [{"display": "2 handfuls of rocket, to serve",
                         "freeText": True}],
    })

    assert _units(auth_client) == []


def test_free_text_row_stores_the_line_verbatim(auth_client):
    line = "a good knob of butter, for finishing"
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Prose", "ingredients": [{"display": line, "freeText": True}],
    }).get_json()["id"]

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"][0]

    assert got["display"] == line
    assert got["food"] is None and got["unit"] is None


def test_free_text_row_is_serialized_as_free_text(auth_client):
    """The editor cannot reproduce the lane on the next edit unless the read
    model says which rows are in it."""
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Prose",
        "ingredients": [{"display": "salt and pepper, to taste", "freeText": True}],
    }).get_json()["id"]

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"][0]

    assert got["freeText"] is True


def test_free_text_row_survives_an_edit_that_resaves_it(auth_client):
    """The editor rebuilds every ingredient from its rows on save, so the real
    question is whether a line that was never touched comes back identical after
    an unrelated edit — and whether it has quietly become a Food by then."""
    line = "a good knob of butter, for finishing"
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Prose",
        "ingredients": [
            {"display": line, "freeText": True},
            {"display": "2 cup flour", "quantity": 2, "unit": "cup", "food": "flour"},
        ],
    }).get_json()["id"]

    # Round-trip the read model back through the writer, the way the editor does.
    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"]
    auth_client.put(f"/api/v1/recipes/{rid}", json={"ingredients": [
        {"display": i["display"], "quantity": i["quantity"],
         "unit": (i["unit"] or {}).get("name", ""),
         "food": (i["food"] or {}).get("name", ""),
         "freeText": i["freeText"], "position": i["position"]}
        for i in got
    ]})

    after = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"]
    assert after[0]["display"] == line
    assert after[0]["freeText"] is True
    assert after[0]["food"] is None
    assert _foods(auth_client) == ["flour"]  # nothing minted from the prose line


def test_free_text_row_ignores_a_structured_unit_name(auth_client):
    """The other unit writer. `test_free_text_row_mints_no_unit` sends only a
    display, so it exercises the display-PARSE writer; a row carrying an
    explicit `unit` takes a different branch that find-or-creates it directly.
    Both must be skipped, and this case had no coverage until a mutation run
    showed the guard could be deleted with every test still green."""
    auth_client.post("/api/v1/recipes", json={
        "name": "Prose",
        "ingredients": [{"display": "a handful of rocket, to serve",
                         "freeText": True, "unit": "handful", "food": "rocket"}],
    })

    assert _units(auth_client) == []
    assert _foods(auth_client) == []


def test_free_text_row_ignores_caller_supplied_food_and_unit_ids(auth_client):
    """A free-text row must not LINK an existing Food/Unit either. Sending ids
    is reachable from any API or MCP client, and a linked food would make the
    row structured again on the next read — the same conversion by another
    door. Also previously uncovered."""
    # arrange: real ids in this group, made by a structured recipe.
    auth_client.post("/api/v1/recipes", json={
        "name": "Structured",
        "ingredients": [{"display": "2 cup flour", "quantity": 2,
                         "unit": "cup", "food": "flour"}],
    })
    food_id = auth_client.get("/api/v1/foods").get_json()[0]["id"]
    unit_id = auth_client.get("/api/v1/units").get_json()[0]["id"]

    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Prose",
        "ingredients": [{"display": "a good knob of butter", "freeText": True,
                         "foodId": food_id, "unitId": unit_id}],
    }).get_json()["id"]

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"][0]
    assert got["food"] is None and got["unit"] is None


def test_a_component_row_is_never_stored_as_free_text(auth_client):
    """A component references a recipe, so there is no prose for it to be.
    Storing both would be a state no reader can represent — every one of them
    gives the component precedence — and the flag would then vanish on the next
    save instead of being resolved once, here."""
    sub = auth_client.post("/api/v1/recipes", json={"name": "Garlic confit"}).get_json()["id"]

    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Uses it",
        "ingredients": [{"display": "1 batch Garlic confit", "freeText": True,
                         "refRecipeId": sub}],
    }).get_json()["id"]

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"][0]
    assert got["refRecipe"]["id"] == sub
    assert got["freeText"] is False


def test_free_text_line_keeps_its_whole_text_on_a_shopping_list(auth_client, app):
    """A free-text row stores no quantity or unit, so the shopping list has
    nothing to prepend — but its no-food branch strips the leading amount out
    of the name on the assumption that it does. That combination deleted the
    author's own words: "2 handfuls of rocket, to serve" was bought as
    "of rocket, to serve" × 0."""
    from app.models import Recipe
    from app.services.shopping import build_from_recipes

    line = "2 handfuls of rocket, to serve"
    auth_client.post("/api/v1/recipes", json={
        "name": "Prose", "ingredients": [{"display": line, "freeText": True}],
    })

    with app.app_context():
        items = build_from_recipes([Recipe.query.filter_by(name="Prose").first()])

    assert [i["display"] for i in items] == [line]


def test_an_empty_food_alone_is_not_treated_as_free_text(auth_client):
    """MUTATION GUARD. An importer leaves `food` empty on a line it could not
    structure, and the editor re-parses those into tidy rows on purpose. If the
    flag is ever derived from emptiness instead of read from the payload, this
    row becomes free text and that re-parse silently stops happening."""
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Legacy",
        "ingredients": [{"display": "a good knob of butter"}],   # no flag, no food
    }).get_json()["id"]

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"][0]

    assert got["freeText"] is False


def test_structured_row_still_mints_its_food(auth_client):
    """Control: the lane must not change the default path."""
    auth_client.post("/api/v1/recipes", json={
        "name": "Structured",
        "ingredients": [{"display": "2 cup flour", "quantity": 2, "unit": "cup",
                         "food": "flour"}],
    })

    assert _foods(auth_client) == ["flour"]
    assert _units(auth_client) == ["cup"]


def test_free_text_row_with_no_number_gets_no_phantom_amount(auth_client):
    """The read view splits `display` into a scannable amount column plus the
    rest. A prose line with no number must leave that column empty rather than
    showing an amount beside text that never had one."""
    line = "a good knob of butter, for finishing"
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Prose", "ingredients": [{"display": line, "freeText": True}],
    }).get_json()["id"]

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"][0]

    assert got["quantity"] == 0
    assert got["amountText"] == "" and got["unitText"] == ""
    assert got["restText"] == line


def test_free_text_row_that_opens_with_a_number_still_splits_losslessly(auth_client):
    """A prose line MAY start with a number ("2 handfuls of rocket"). Showing
    that number in the amount column is correct — it is the author's own text,
    and it is removed from restText, so the row still reads as one line. What
    must not happen is the number surviving in both halves or vanishing."""
    line = "2 handfuls of rocket, to serve"
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Prose", "ingredients": [{"display": line, "freeText": True}],
    }).get_json()["id"]

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"][0]

    assert f'{got["amountText"]} {got["unitText"]} {got["restText"]}'.split() \
        == line.split()


def test_version_restore_preserves_a_free_text_line(auth_client):
    """Any field the update endpoint accepts but the snapshot omits is silently
    wiped on restore — which for this flag means the line comes back as a
    structured row and mints its Food after all."""
    line = "a good knob of butter, for finishing"
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Prose", "ingredients": [{"display": line, "freeText": True}],
    }).get_json()["id"]

    # An edit creates the pre-edit auto snapshot, which we then restore.
    auth_client.put(f"/api/v1/recipes/{rid}", json={"name": "Prose edited"})
    vid = auth_client.get(f"/api/v1/recipes/{rid}/versions").get_json()["items"][0]["id"]
    auth_client.post(f"/api/v1/recipes/{rid}/versions/{vid}/restore")

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"][0]
    assert got["display"] == line
    assert got["freeText"] is True
    assert _foods(auth_client) == []
