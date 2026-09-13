

def test_list_entries_limit_bounds_the_result(auth_client):
    """The empty-state probe asks "has anything EVER been planned?", which
    without a bound means serializing the household's entire history to answer
    a yes/no. `limit` keeps that cheap."""
    r = auth_client.post("/api/v1/recipes", json={"name": "Soup"}).get_json()
    for d in ("2026-01-05", "2026-01-06", "2026-01-07"):
        auth_client.post("/api/v1/mealplans",
                         json={"date": d, "recipeId": r["id"]})

    assert len(auth_client.get("/api/v1/mealplans").get_json()["items"]) == 3
    assert len(auth_client.get(
        "/api/v1/mealplans?limit=1").get_json()["items"]) == 1
    # A hostile or silly limit can neither return zero rows nor unbounded ones.
    assert len(auth_client.get(
        "/api/v1/mealplans?limit=0").get_json()["items"]) == 1
    assert len(auth_client.get(
        "/api/v1/mealplans?limit=99999").get_json()["items"]) == 3
