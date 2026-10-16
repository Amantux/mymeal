"""Step structure through the route, and the `title` round-trip that guards it.

The pure grouping/extraction logic is covered in test_step_structure.py. These
tests are about the boundary: that the recipe detail response carries the
structure, and that an author's own step headings survive a save — the thing
that made `title` a dead column in practice.
"""
from test_step_structure import FOCACCIA


def _create_focaccia(auth_client, titles=None):
    titles = titles or [""] * len(FOCACCIA)
    body = {
        "name": "Cast Iron Focaccia",
        "steps": [{"title": t, "text": x} for t, x in zip(titles, FOCACCIA)],
    }
    r = auth_client.post("/api/v1/recipes", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def test_recipe_detail_carries_derived_phase_groups(auth_client):
    rid = _create_focaccia(auth_client)

    structure = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["stepStructure"]

    assert [g["title"] for g in structure["groups"]] == [
        "Mix", "Fold", "Chill", "Shape", "Proof", "Top & bake", "Finish",
    ]
    assert structure["summary"]["derivedHeadings"] is True


def test_recipe_detail_surfaces_the_waiting_total(auth_client):
    rid = _create_focaccia(auth_client)

    summary = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["stepStructure"]["summary"]

    assert summary["waitText"] == "15 hr 5 min–3 days 3 hr"
    assert summary["waitingSteps"] == 5


def test_step_chip_shows_the_fridge_range_on_its_own_step(auth_client):
    rid = _create_focaccia(auth_client)

    rows = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["stepStructure"]["steps"]

    assert [d["label"] for d in rows[3]["durations"]] == ["40 min", "12–72 hr"]
    assert rows[7]["temperature"] == "230°C / 450°F"


def test_steps_list_is_unchanged_by_the_structure_view(auth_client):
    """`steps` stays the authoritative unannotated list, so every existing
    consumer (MCP, HA, the share page) is untouched by this addition."""
    rid = _create_focaccia(auth_client)

    body = auth_client.get(f"/api/v1/recipes/{rid}").get_json()

    assert [s["text"] for s in body["steps"]] == FOCACCIA


def test_author_step_titles_survive_a_save(auth_client):
    """The regression this change exists to prevent. `RecipeStep.title` was
    populated by the schema.org and AI import paths but dropped by the editor's
    load/save round-trip, so a cook's own headings vanished the first time they
    touched the recipe — which is why the column looked unused.
    """
    titles = ["Dough"] + [""] * 6 + ["Bake day"] + [""] * 4
    rid = _create_focaccia(auth_client, titles)
    body = auth_client.get(f"/api/v1/recipes/{rid}").get_json()

    # Re-save exactly what the detail view handed us, as the editor does.
    saved = auth_client.put(f"/api/v1/recipes/{rid}", json={
        "steps": [{"title": s["title"], "text": s["text"]} for s in body["steps"]],
    })
    assert saved.status_code == 200

    reloaded = auth_client.get(f"/api/v1/recipes/{rid}").get_json()
    assert [s["title"] for s in reloaded["steps"]] == titles
    assert [s["text"] for s in reloaded["steps"]] == FOCACCIA


def test_author_titles_round_trip_and_override_inference(auth_client):
    titles = ["Dough"] + [""] * 6 + ["Bake day"] + [""] * 4
    rid = _create_focaccia(auth_client, titles)

    body = auth_client.get(f"/api/v1/recipes/{rid}").get_json()

    assert [s["title"] for s in body["steps"]] == titles
    # An author heading anywhere switches the whole method to author mode: no
    # inferred heading is mixed in under identical styling.
    groups = body["stepStructure"]["groups"]
    # Steps the author left untitled stay in unheaded runs rather than being
    # given an inferred label, including the trailing one.
    assert [g["title"] for g in groups] == ["Dough", "", "Bake day", ""]
    assert not any(g["derived"] for g in groups)


def test_short_method_has_no_headings(auth_client):
    r = auth_client.post("/api/v1/recipes", json={
        "name": "Toast",
        "steps": [{"text": "Toast the bread."}, {"text": "Butter it."}],
    })
    rid = r.get_json()["id"]

    structure = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["stepStructure"]

    assert structure["summary"]["grouped"] is False
    assert [g["title"] for g in structure["groups"]] == [""]


def test_recipe_with_no_steps_is_safe(auth_client):
    rid = auth_client.post("/api/v1/recipes", json={"name": "Empty"}).get_json()["id"]

    structure = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["stepStructure"]

    assert structure["steps"] == []
    assert structure["summary"]["totalText"] == ""
