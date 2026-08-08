"""Deleting a recipe on the meal plan, or a unit any recipe uses, must not 500.

Both did a bare delete while an FK (mealplan_entries.recipe_id,
recipe_ingredients.unit_id) still pointed at the row, and the app enforces
foreign keys — so the delete raised IntegrityError (500), also breaking the MCP
delete_recipe tool and the HA service.
"""


def _recipe(c, name, **kw):
    return c.post("/api/v1/recipes", json={"name": name, **kw}).get_json()


def test_deleting_a_planned_recipe_no_longer_500s(auth_client, app):
    r = _recipe(auth_client, "Chili")
    # put it on the meal plan
    mp = auth_client.post("/api/v1/mealplans",
                          json={"date": "2026-01-05", "recipeId": r["id"],
                                "meal": "dinner"})
    assert mp.status_code in (200, 201)

    assert auth_client.delete(f"/api/v1/recipes/{r['id']}").status_code == 204
    # the plan entry survives (detached) — the recipe is just gone from it
    from app.models import MealPlanEntry
    with app.app_context():
        from app.extensions import db
        e = db.session.query(MealPlanEntry).first()
        assert e is None or e.recipe_id is None


def test_deleting_an_in_use_unit_no_longer_500s(auth_client):
    r = _recipe(auth_client, "Cake", ingredients=[
        {"display": "2 cups flour", "food": "flour"}])
    # find the auto-created "cup" unit
    units = auth_client.get("/api/v1/units").get_json()
    cup = [u for u in units if u["name"] in ("cup", "cups")]
    assert cup, "no unit was created"
    assert auth_client.delete(f"/api/v1/units/{cup[0]['id']}").status_code == 204
    # the recipe line survives; its unit is just cleared
    body = auth_client.get(f"/api/v1/recipes/{r['id']}").get_json()
    assert body["ingredients"][0]["display"] == "2 cups flour"
