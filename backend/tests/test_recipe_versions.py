"""Recipe versioning: auto edit-history + experiment branches (edit/feedback/
promote/restore), snapshot round-trip, dangling-ref safety, and IDOR."""


def _new_recipe(client, name="Soup"):
    return client.post("/api/v1/recipes", json={
        "name": name,
        "servings": 4,
        "ingredients": [{"display": "2 onions", "food": "onion", "quantity": 2}],
        "steps": [{"text": "Chop"}],
    }).get_json()["id"]


def _other_client(app):
    """A second user in their OWN group (for IDOR)."""
    c = app.test_client()
    c.post("/api/v1/users/register",
           json={"email": "u2@t.com", "password": "password", "name": "U2"})
    token = c.post("/api/v1/users/login",
                   json={"username": "u2@t.com", "password": "password"}).get_json()["token"]
    c.environ_base["HTTP_AUTHORIZATION"] = token
    return c


def _versions(client, rid):
    return client.get(f"/api/v1/recipes/{rid}/versions").get_json()["items"]


# --- auto history ----------------------------------------------------------

def test_update_creates_one_auto_version(auth_client):
    rid = _new_recipe(auth_client)
    assert _versions(auth_client, rid) == []          # nothing yet
    auth_client.put(f"/api/v1/recipes/{rid}", json={"name": "Soup v2"})
    vs = _versions(auth_client, rid)
    assert len(vs) == 1 and vs[0]["kind"] == "auto"


def test_auto_versions_pruned_to_cap(auth_client):
    from app.api.recipe_versions import MAX_AUTO_VERSIONS
    rid = _new_recipe(auth_client)
    for i in range(MAX_AUTO_VERSIONS + 5):
        auth_client.put(f"/api/v1/recipes/{rid}", json={"notes": f"edit {i}"})
    autos = [v for v in _versions(auth_client, rid) if v["kind"] == "auto"]
    assert len(autos) == MAX_AUTO_VERSIONS


# --- experiments -----------------------------------------------------------

def test_experiment_edit_does_not_touch_live_recipe(auth_client):
    rid = _new_recipe(auth_client)
    vid = auth_client.post(f"/api/v1/recipes/{rid}/versions",
                           json={"label": "More thyme"}).get_json()["id"]
    # edit the experiment snapshot only
    auth_client.put(f"/api/v1/recipes/{rid}/versions/{vid}", json={
        "name": "Thyme Soup",
        "ingredients": [{"display": "3 onions", "food": "onion", "quantity": 3},
                        {"display": "thyme", "food": "thyme", "quantity": 1}],
        "steps": [{"text": "Chop"}],
    })
    live = auth_client.get(f"/api/v1/recipes/{rid}").get_json()
    assert live["name"] == "Soup"                      # live recipe unchanged
    assert len(live["ingredients"]) == 1
    snap = auth_client.get(f"/api/v1/recipes/{rid}/versions/{vid}").get_json()["snapshot"]
    assert snap["name"] == "Thyme Soup" and len(snap["ingredients"]) == 2


def test_experiment_feedback(auth_client):
    rid = _new_recipe(auth_client)
    vid = auth_client.post(f"/api/v1/recipes/{rid}/versions", json={}).get_json()["id"]
    r = auth_client.post(f"/api/v1/recipes/{rid}/versions/{vid}/feedback",
                         json={"rating": 5, "feedback": "best batch yet"})
    body = r.get_json()
    assert body["rating"] == 5 and body["feedback"] == "best batch yet"


def test_promote_experiment_updates_live_and_is_reversible(auth_client):
    rid = _new_recipe(auth_client)
    vid = auth_client.post(f"/api/v1/recipes/{rid}/versions",
                           json={"label": "v2"}).get_json()["id"]
    auth_client.put(f"/api/v1/recipes/{rid}/versions/{vid}", json={
        "name": "Promoted Soup",
        "ingredients": [{"display": "5 onions", "food": "onion", "quantity": 5}],
        "steps": [{"text": "Chop then simmer"}],
    })
    before = auth_client.get(f"/api/v1/recipes/{rid}").get_json()
    assert before["name"] == "Soup"
    promoted = auth_client.post(f"/api/v1/recipes/{rid}/versions/{vid}/promote").get_json()
    assert promoted["name"] == "Promoted Soup"
    assert float(promoted["ingredients"][0]["quantity"]) == 5
    # experiment marked promoted + a pre-promote auto snapshot exists
    vs = _versions(auth_client, rid)
    exp = next(v for v in vs if v["id"] == vid)
    assert exp["status"] == "promoted"
    assert any(v["kind"] == "auto" for v in vs)


def test_rest_restore_round_trips(auth_client):
    rid = _new_recipe(auth_client, "Original")
    # edit → creates an auto snapshot of "Original"
    auth_client.put(f"/api/v1/recipes/{rid}", json={"name": "Changed"})
    auto = next(v for v in _versions(auth_client, rid) if v["kind"] == "auto")
    restored = auth_client.post(
        f"/api/v1/recipes/{rid}/versions/{auto['id']}/restore").get_json()
    assert restored["name"] == "Original"
    assert len(restored["ingredients"]) == 1


def test_restore_nulls_a_dangling_component_ref(auth_client):
    base = _new_recipe(auth_client, "Stock")
    soup = auth_client.post("/api/v1/recipes", json={
        "name": "Onion Soup",
        "ingredients": [{"display": "1 batch stock", "refRecipeId": base}],
        "steps": [{"text": "Combine"}],
    }).get_json()["id"]
    vid = auth_client.post(f"/api/v1/recipes/{soup}/versions", json={}).get_json()["id"]
    # delete the linked sub-recipe, then promote the snapshot that still references it
    auth_client.delete(f"/api/v1/recipes/{base}")
    r = auth_client.post(f"/api/v1/recipes/{soup}/versions/{vid}/promote")
    assert r.status_code == 200            # no crash
    out = r.get_json()
    assert all(i.get("refRecipe") is None for i in out["ingredients"])  # ref dropped


# --- IDOR ------------------------------------------------------------------

def test_version_endpoints_are_group_scoped(auth_client, app):
    rid = _new_recipe(auth_client)
    vid = auth_client.post(f"/api/v1/recipes/{rid}/versions", json={}).get_json()["id"]
    other = _other_client(app)
    assert other.get(f"/api/v1/recipes/{rid}/versions").status_code == 404
    assert other.get(f"/api/v1/recipes/{rid}/versions/{vid}").status_code == 404
    assert other.put(f"/api/v1/recipes/{rid}/versions/{vid}", json={}).status_code == 404
    assert other.post(
        f"/api/v1/recipes/{rid}/versions/{vid}/promote").status_code == 404
    assert other.delete(f"/api/v1/recipes/{rid}/versions/{vid}").status_code == 404
    # the owner's version is untouched
    assert auth_client.get(f"/api/v1/recipes/{rid}/versions/{vid}").status_code == 200


# --- reviewer-suggested coverage: full-fidelity round-trip + cross-recipe guard ---

def test_restore_preserves_tags_categories_nutrition_and_ingredient_meta(auth_client):
    # A restore must be lossless across ALL snapshotted fields, not just name/qty.
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Rich Soup",
        "servings": 6,
        "nutrition": {"calories": 320, "protein": 12},
        "tags": ["french", "winter"],
        "ingredients": [{"display": "2 onions", "food": "onion", "quantity": 2,
                         "section": "For the base", "note": "thinly sliced"}],
        "steps": [{"title": "Prep", "text": "Chop the onions"}],
    }).get_json()["id"]
    # Snapshot the rich state (an update creates an auto version of the pre-edit state).
    auth_client.put(f"/api/v1/recipes/{rid}", json={"name": "Wrecked",
                    "nutrition": {}, "tags": [], "ingredients": [], "steps": []})
    vid = [v for v in _versions(auth_client, rid) if v["kind"] == "auto"][0]["id"]
    auth_client.post(f"/api/v1/recipes/{rid}/versions/{vid}/restore")
    r = auth_client.get(f"/api/v1/recipes/{rid}").get_json()
    assert r["name"] == "Rich Soup" and r["servings"] == 6
    assert r["nutrition"] == {"calories": 320, "protein": 12}
    assert sorted(t["name"] for t in r["tags"]) == ["french", "winter"]
    ing = r["ingredients"][0]
    assert ing["quantity"] == 2 and ing["section"] == "For the base"
    assert ing["note"] == "thinly sliced"
    assert r["steps"][0]["title"] == "Prep"


def test_cannot_promote_a_version_from_another_recipe_same_group(auth_client):
    # A version id belonging to recipe B must not be promotable onto recipe A,
    # even within the same group (the v.recipe_id == recipe.id guard).
    rid_a = _new_recipe(auth_client, "Alpha")
    rid_b = _new_recipe(auth_client, "Bravo")
    vid_b = auth_client.post(f"/api/v1/recipes/{rid_b}/versions",
                             json={"label": "exp"}).get_json()["id"]
    r = auth_client.post(f"/api/v1/recipes/{rid_a}/versions/{vid_b}/promote")
    assert r.status_code == 404
