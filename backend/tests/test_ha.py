"""M5: Home Assistant read endpoints (/ha/summary, /ha/calendar)."""


def test_ha_summary_shape(auth_client):
    r = auth_client.get("/api/v1/recipes", json={})  # ensure group exists
    assert r.status_code == 200
    auth_client.post("/api/v1/recipes", json={"name": "Soup"})
    auth_client.post("/api/v1/pantry", json={"label": "rice"})

    s = auth_client.get("/api/v1/ha/summary").get_json()
    assert s["health"] is True
    assert s["totals"]["recipes"] == 1
    assert s["totals"]["pantryItems"] == 1
    assert "todaysMeals" in s and "weekPlan" in s


def test_ha_calendar_returns_meal_entries(auth_client):
    r = auth_client.post("/api/v1/recipes", json={"name": "Pie"}).get_json()
    auth_client.post(
        "/api/v1/mealplans",
        json={"date": "2026-07-20", "mealType": "dinner", "recipeId": r["id"]},
    )
    events = auth_client.get(
        "/api/v1/ha/calendar?start=2026-07-19&end=2026-07-21"
    ).get_json()
    assert len(events) == 1
    assert events[0]["summary"] == "Dinner: Pie"
    assert events[0]["start"] == "2026-07-20"


def test_ha_endpoints_require_auth(client):
    assert client.get("/api/v1/ha/summary").status_code == 401
    assert client.get("/api/v1/ha/calendar").status_code == 401
