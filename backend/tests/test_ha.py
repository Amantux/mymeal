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


# --- Home Assistant ingress: no sign-in required ---------------------------


def test_auth_mode_reports_login_required_when_auth_is_on(client):
    """Standalone deployment: the SPA must be told to show the login screen."""
    r = client.get("/api/v1/misc/auth-mode")
    assert r.status_code == 200
    body = r.get_json()
    assert body["authDisabled"] is False
    assert body["loginRequired"] is True


def test_auth_mode_reports_no_login_behind_ingress(noauth_app):
    """Add-on/ingress deployment (disable_auth: true): HA has already
    authenticated the user, so the SPA must never render a login screen."""
    r = noauth_app.test_client().get("/api/v1/misc/auth-mode")
    assert r.status_code == 200
    body = r.get_json()
    assert body["authDisabled"] is True
    assert body["loginRequired"] is False


def test_auth_mode_is_reachable_without_a_token(client):
    """It is the SPA's very first call — requiring auth would deadlock the
    bootstrap it exists to unblock."""
    r = client.get("/api/v1/misc/auth-mode")
    assert r.status_code == 200
    assert "Authorization" not in dict(client.environ_base or {})


def test_auth_mode_does_not_leak_secrets(noauth_app):
    """It is unauthenticated, so it must expose only the auth posture."""
    body = noauth_app.test_client().get("/api/v1/misc/auth-mode").get_json()
    assert set(body) == {"authDisabled", "loginRequired", "allowRegistration"}
    blob = str(body).lower()
    for leak in ("secret", "token", "password", "key"):
        assert leak not in blob


def test_ingress_user_reaches_the_api_with_no_credentials(noauth_app):
    """End-to-end shape of the ingress path: no token anywhere, real data back."""
    c = noauth_app.test_client()
    assert c.get("/api/v1/misc/auth-mode").get_json()["authDisabled"] is True
    assert c.get("/api/v1/users/self").status_code == 200
    assert c.get("/api/v1/mealplans").status_code == 200


def test_auth_still_enforced_when_not_behind_ingress(client):
    """The ingress convenience must not weaken the standalone deployment."""
    assert client.get("/api/v1/users/self").status_code == 401
    assert client.get("/api/v1/recipes").status_code == 401
