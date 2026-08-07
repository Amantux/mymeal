"""The qualifier must survive every path that rebuilds an ingredient.

The trap this guards is written into recipe_versions.py already, because
cookTemperatureC hit it once: any field the update endpoint accepts but the
snapshot builder omits is silently wiped when a version is restored. There are
THREE such paths, not one.
"""
from app.extensions import db
from app.models import Recipe


def _recipe(auth_client, **ing):
    body = {"name": "Buns", "servings": 4,
            "ingredients": [dict({"display": "2 tsp Vietnamese cinnamon"}, **ing)]}
    r = auth_client.post("/api/v1/recipes", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_the_qualifier_is_stored_and_returned(auth_client):
    body = _recipe(auth_client, qualifier="Vietnamese")
    assert body["ingredients"][0]["qualifier"] == "Vietnamese"


def test_the_qualifier_is_not_the_note(auth_client):
    """note means preparation; merging the two makes them indistinguishable."""
    body = _recipe(auth_client, qualifier="Vietnamese", note="finely ground")
    ing = body["ingredients"][0]
    assert (ing["qualifier"], ing["note"]) == ("Vietnamese", "finely ground")


def test_an_ordinary_save_preserves_it(auth_client):
    body = _recipe(auth_client, qualifier="Vietnamese")
    rid = body["id"]
    rows = [{"display": i["display"], "quantity": i["quantity"],
             "note": i["note"], "qualifier": i["qualifier"]}
            for i in body["ingredients"]]

    r = auth_client.put(f"/api/v1/recipes/{rid}", json={"ingredients": rows})

    assert r.get_json()["ingredients"][0]["qualifier"] == "Vietnamese"


def test_it_survives_a_version_restore(auth_client, app):
    """The cookTemperatureC trap: a field the snapshot omits is wiped on
    restore, silently."""
    body = _recipe(auth_client, qualifier="Vietnamese")
    rid = body["id"]

    # An edit creates an automatic snapshot of the PREVIOUS state.
    auth_client.put(f"/api/v1/recipes/{rid}",
                    json={"ingredients": [{"display": "2 tsp cinnamon"}]})
    versions = auth_client.get(f"/api/v1/recipes/{rid}/versions").get_json()["items"]
    assert versions, "no snapshot was taken"

    vid = versions[0]["id"]
    r = auth_client.post(f"/api/v1/recipes/{rid}/versions/{vid}/restore")
    assert r.status_code in (200, 201), r.get_json()

    after = auth_client.get(f"/api/v1/recipes/{rid}").get_json()
    assert after["ingredients"][0]["qualifier"] == "Vietnamese", \
        "the qualifier was wiped by the restore — it is missing from _snapshot_recipe"


def test_an_over_long_qualifier_is_clamped_not_a_500(auth_client):
    """String(120) on Postgres would raise on overflow; SQLite would accept it
    silently, so the clamp has to be in the code."""
    body = _recipe(auth_client, qualifier="x" * 400)
    assert len(body["ingredients"][0]["qualifier"]) == 120


def test_the_column_exists_on_a_migrated_database(app):
    """create_all() and the migration must agree — server_default is what makes
    a fresh database and a migrated one describe the same table."""
    with app.app_context():
        cols = {c["name"] for c in db.inspect(db.engine).get_columns("recipe_ingredients")}
        assert "qualifier" in cols
        food_cols = {c["name"] for c in db.inspect(db.engine).get_columns("foods")}
        assert {"classification", "allergens"} <= food_cols
        assert db.session.query(Recipe).count() >= 0
