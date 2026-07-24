"""'Use it up' — recipes that consume soon-to-expire Edibl stock (feature #3)."""
from datetime import date, timedelta

from app.services.edibl import EdiblClient


def _make_recipe(client, name, ingredients):
    return client.post(
        "/api/v1/recipes",
        json={"name": name, "ingredients": [{"display": i} for i in ingredients]},
    ).get_json()


def test_use_it_up_unavailable_without_edibl(auth_client, monkeypatch):
    monkeypatch.setattr(EdiblClient, "on_hand",
                        lambda self: {"available": False, "items": [], "reason": "not configured"})
    body = auth_client.post("/api/v1/ai/use-it-up").get_json()
    assert body["ediblAvailable"] is False
    assert body["suggestions"] == [] and body["expiring"] == []


def test_use_it_up_ranks_recipes_using_expiring_items(auth_client, monkeypatch):
    _make_recipe(auth_client, "Spinach Omelette", ["2 eggs", "spinach"])
    _make_recipe(auth_client, "Plain Toast", ["bread", "butter"])

    soon = (date.today() + timedelta(days=2)).isoformat()
    later = (date.today() + timedelta(days=40)).isoformat()
    monkeypatch.setattr(EdiblClient, "on_hand", lambda self: {
        "available": True,
        "items": [
            {"name": "spinach", "expiresAt": soon},
            {"name": "quinoa", "expiresAt": later},   # outside the 5-day window
        ],
    })

    body = auth_client.post("/api/v1/ai/use-it-up").get_json()
    assert body["ediblAvailable"] is True
    # Only spinach is within the window; quinoa (40d) is excluded.
    assert [e["name"] for e in body["expiring"]] == ["spinach"]
    assert body["expiring"][0]["daysLeft"] == 2
    # Only the recipe that uses spinach is suggested.
    names = [s["name"] for s in body["suggestions"]]
    assert names == ["Spinach Omelette"]
    assert body["suggestions"][0]["soonestDaysLeft"] == 2


def test_use_it_up_excludes_already_far_items_and_respects_days_param(auth_client, monkeypatch):
    _make_recipe(auth_client, "Quinoa Bowl", ["quinoa", "oil"])
    later = (date.today() + timedelta(days=40)).isoformat()
    monkeypatch.setattr(EdiblClient, "on_hand", lambda self: {
        "available": True, "items": [{"name": "quinoa", "expiresAt": later}]})

    # Default window (5) excludes it…
    assert auth_client.post("/api/v1/ai/use-it-up").get_json()["suggestions"] == []
    # …a wider window includes it.
    wide = auth_client.post("/api/v1/ai/use-it-up", json={"days": 45}).get_json()
    assert [s["name"] for s in wide["suggestions"]] == ["Quinoa Bowl"]
