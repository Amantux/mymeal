"""The public meal-plan iCalendar feed: token lifecycle, tenancy, ICS content.

The feed is the only unauthenticated route that serves a household's own data,
so the tenancy and token tests here carry real weight — each guard is
mutation-checked in CI-less fashion by the reviewer, and the cross-group test
is the first attack anyone will try.
"""
from datetime import date, timedelta

import pytest

from app.extensions import db


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _login(client, email):
    client.post("/api/v1/users/register",
                json={"email": email, "password": "password", "name": email})
    return client.post("/api/v1/users/login",
                       json={"username": email, "password": "password"}).get_json()["token"]


def _recipe(client, name="Chilli"):
    return client.post(
        "/api/v1/recipes",
        json={"name": name, "ingredients": [{"display": "1 bean"}],
              "steps": [{"text": "Cook"}]},
    ).get_json()


def _entry(client, day, **kw):
    body = {"date": day.isoformat()}
    body.update(kw)
    return client.post("/api/v1/mealplans", json=body).get_json()


def _publish(client):
    return client.post("/api/v1/calendar/subscription").get_json()["token"]


def _fetch(client, token):
    return client.application.test_client().get(f"/api/v1/calendar/{token}.ics")


def _events(text):
    """Split a VCALENDAR into its VEVENT blocks (unfolded)."""
    unfolded = text.replace("\r\n ", "")
    blocks, cur = [], None
    for line in unfolded.split("\r\n"):
        if line == "BEGIN:VEVENT":
            cur = []
        elif line == "END:VEVENT":
            blocks.append(cur)
            cur = None
        elif cur is not None:
            cur.append(line)
    return blocks


TODAY = date.today()


# --------------------------------------------------------------------------
# token lifecycle
# --------------------------------------------------------------------------

def test_subscription_is_unpublished_until_created(auth_client):
    assert auth_client.get("/api/v1/calendar/subscription").get_json()["token"] is None


def test_create_subscription_mints_a_high_entropy_token(auth_client):
    token = _publish(auth_client)

    assert token and len(token) >= 32


def test_create_subscription_is_idempotent(auth_client):
    first = _publish(auth_client)

    second = _publish(auth_client)

    # Re-opening the dialog must never break a live subscription.
    assert first == second


def test_rotate_issues_a_new_token(auth_client):
    first = _publish(auth_client)

    second = auth_client.post(
        "/api/v1/calendar/subscription/rotate").get_json()["token"]

    assert second != first


def test_rotate_invalidates_the_old_url(auth_client):
    first = _publish(auth_client)
    assert _fetch(auth_client, first).status_code == 200

    second = auth_client.post(
        "/api/v1/calendar/subscription/rotate").get_json()["token"]

    assert _fetch(auth_client, first).status_code == 404
    assert _fetch(auth_client, second).status_code == 200


def test_delete_subscription_unpublishes_the_feed(auth_client):
    token = _publish(auth_client)
    assert _fetch(auth_client, token).status_code == 200

    assert auth_client.delete("/api/v1/calendar/subscription").status_code == 204

    assert _fetch(auth_client, token).status_code == 404
    assert auth_client.get(
        "/api/v1/calendar/subscription").get_json()["token"] is None


def test_managing_the_subscription_requires_auth(client):
    assert client.get("/api/v1/calendar/subscription").status_code == 401
    assert client.post("/api/v1/calendar/subscription").status_code == 401
    assert client.post("/api/v1/calendar/subscription/rotate").status_code == 401
    assert client.delete("/api/v1/calendar/subscription").status_code == 401


# --------------------------------------------------------------------------
# unauthenticated access
# --------------------------------------------------------------------------

def test_feed_is_fetchable_with_no_credentials_at_all(auth_client):
    _entry(auth_client, TODAY, title="Toast")
    token = _publish(auth_client)

    # A brand-new client with no Authorization header — a calendar app cannot
    # send one, which is the entire reason this route exists.
    anon = auth_client.application.test_client()
    res = anon.get(f"/api/v1/calendar/{token}.ics")

    assert res.status_code == 200
    assert res.get_data(as_text=True).startswith("BEGIN:VCALENDAR")


def test_feed_serves_the_icalendar_content_type(auth_client):
    token = _publish(auth_client)

    res = _fetch(auth_client, token)

    assert res.headers["Content-Type"] == "text/calendar; charset=utf-8"
    assert res.headers["X-Content-Type-Options"] == "nosniff"


def test_feed_forbids_caching_so_subscribers_never_see_a_stale_plan(auth_client):
    token = _publish(auth_client)

    res = _fetch(auth_client, token)

    assert "no-store" in res.headers["Cache-Control"]


@pytest.mark.parametrize("token", ["nope", "x" * 60, "null", "None"])
def test_unknown_token_returns_404_not_401(auth_client, token):
    _publish(auth_client)

    res = _fetch(auth_client, token)

    # 404 rather than 401/403: a distinct status would confirm the token
    # namespace exists and make the endpoint an oracle.
    assert res.status_code == 404


def test_a_missing_token_segment_is_not_routable(auth_client):
    # Honest about what this proves: the URL converter requires >=1 character,
    # so this 404s in routing and never reaches the view. The view's own
    # empty-token guard is belt-and-braces for non-HTTP callers and is not what
    # is being asserted here.
    anon = auth_client.application.test_client()

    assert anon.get("/api/v1/calendar/.ics").status_code == 404


# --------------------------------------------------------------------------
# tenancy — the first attack a reviewer makes
# --------------------------------------------------------------------------

def test_feed_never_leaks_another_households_meals(client):
    # Household A plans something secret and publishes a feed.
    client.environ_base["HTTP_AUTHORIZATION"] = _login(client, "a@a.com")
    _entry(client, TODAY, title="Secret Lasagne")
    a_token = _publish(client)

    # Household B plans something of its own and publishes too.
    client.environ_base["HTTP_AUTHORIZATION"] = _login(client, "b@b.com")
    _entry(client, TODAY, title="Bravo Burgers")
    b_token = _publish(client)

    a_body = _fetch(client, a_token).get_data(as_text=True)
    b_body = _fetch(client, b_token).get_data(as_text=True)

    assert "Secret Lasagne" in a_body and "Bravo Burgers" not in a_body
    assert "Bravo Burgers" in b_body and "Secret Lasagne" not in b_body


def test_each_group_gets_a_distinct_token(client):
    client.environ_base["HTTP_AUTHORIZATION"] = _login(client, "a@a.com")
    a_token = _publish(client)
    client.environ_base["HTTP_AUTHORIZATION"] = _login(client, "b@b.com")

    b_token = _publish(client)

    assert a_token != b_token


def test_rotating_one_group_does_not_touch_another(client):
    client.environ_base["HTTP_AUTHORIZATION"] = _login(client, "a@a.com")
    a_token = _publish(client)
    client.environ_base["HTTP_AUTHORIZATION"] = _login(client, "b@b.com")
    b_token = _publish(client)

    client.post("/api/v1/calendar/subscription/rotate")

    assert _fetch(client, a_token).status_code == 200
    assert _fetch(client, b_token).status_code == 404


# --------------------------------------------------------------------------
# window
# --------------------------------------------------------------------------

def test_feed_includes_recent_past_and_all_future_but_not_the_archive(auth_client):
    _entry(auth_client, TODAY - timedelta(days=400), title="Ancient Gruel")
    _entry(auth_client, TODAY - timedelta(days=29), title="Just Too Old")
    _entry(auth_client, TODAY - timedelta(days=27), title="Recent Stew")
    _entry(auth_client, TODAY, title="Today Pie")
    _entry(auth_client, TODAY + timedelta(days=300), title="Distant Roast")
    token = _publish(auth_client)

    body = _fetch(auth_client, token).get_data(as_text=True)

    assert "Recent Stew" in body
    assert "Today Pie" in body
    assert "Distant Roast" in body      # no forward bound — plan as far as you like
    assert "Just Too Old" not in body   # 28-day boundary is exclusive of day 29
    assert "Ancient Gruel" not in body


def test_empty_plan_yields_a_valid_empty_calendar_not_an_error(auth_client):
    token = _publish(auth_client)

    res = _fetch(auth_client, token)

    # A 404 here would render as "your calendar is broken" in every client.
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert body.startswith("BEGIN:VCALENDAR") and body.endswith("END:VCALENDAR\r\n")
    assert "BEGIN:VEVENT" not in body


# --------------------------------------------------------------------------
# ICS content
# --------------------------------------------------------------------------

def test_event_is_all_day_with_an_exclusive_dtend(auth_client):
    _entry(auth_client, date(2099, 3, 14), title="Pi Pie")
    token = _publish(auth_client)

    ev = _events(_fetch(auth_client, token).get_data(as_text=True))[0]

    assert "DTSTART;VALUE=DATE:20990314" in ev
    assert "DTEND;VALUE=DATE:20990315" in ev


def test_summary_carries_the_meal_slot_and_the_recipe_name(auth_client):
    rid = _recipe(auth_client, "Chilli con carne")["id"]
    _entry(auth_client, TODAY, recipeId=rid, mealType="breakfast")
    token = _publish(auth_client)

    ev = _events(_fetch(auth_client, token).get_data(as_text=True))[0]

    assert "SUMMARY:Breakfast: Chilli con carne" in ev


def test_summary_falls_back_to_the_free_text_title_without_a_recipe(auth_client):
    _entry(auth_client, TODAY, title="Leftovers", mealType="lunch")
    token = _publish(auth_client)

    ev = _events(_fetch(auth_client, token).get_data(as_text=True))[0]

    assert "SUMMARY:Lunch: Leftovers" in ev


def test_description_carries_servings_notes_and_the_recipe_slug(auth_client):
    rid = _recipe(auth_client, "Roast Chicken")["id"]
    _entry(auth_client, TODAY, recipeId=rid, servings=4, notes="use the big tin")
    token = _publish(auth_client)

    ev = _events(_fetch(auth_client, token).get_data(as_text=True))[0]
    desc = next(line for line in ev if line.startswith("DESCRIPTION:"))

    assert "Serves 4" in desc
    assert "use the big tin" in desc
    assert "roast-chicken" in desc


def test_description_emits_no_url_because_none_can_be_known(auth_client):
    # The backend cannot know its own externally-reachable URL (ingress path is
    # random and session-scoped), so a guessed link would be dead in every
    # event. Asserting the absence keeps a future "helpful" addition honest.
    rid = _recipe(auth_client, "Roast Chicken")["id"]
    _entry(auth_client, TODAY, recipeId=rid)
    token = _publish(auth_client)

    body = _fetch(auth_client, token).get_data(as_text=True)

    assert "http://" not in body and "https://" not in body


def test_uid_is_the_entry_id_and_is_stable_across_fetches(auth_client):
    entry = _entry(auth_client, TODAY, title="Stew")
    token = _publish(auth_client)

    first = _events(_fetch(auth_client, token).get_data(as_text=True))[0]
    second = _events(_fetch(auth_client, token).get_data(as_text=True))[0]

    uid = next(line for line in first if line.startswith("UID:"))
    assert uid == f"UID:{entry['id']}@mymeal"
    # Stability is the whole point: an unstable UID makes every poll duplicate
    # the calendar instead of updating it.
    assert uid in second


def test_uid_survives_editing_the_entry(auth_client):
    entry = _entry(auth_client, TODAY, title="Stew")
    token = _publish(auth_client)
    before = _events(_fetch(auth_client, token).get_data(as_text=True))[0]

    auth_client.put(f"/api/v1/mealplans/{entry['id']}",
                    json={"title": "Better Stew",
                          "date": (TODAY + timedelta(days=1)).isoformat()})

    after = _events(_fetch(auth_client, token).get_data(as_text=True))[0]
    uid = f"UID:{entry['id']}@mymeal"
    assert uid in before and uid in after
    assert any("Better Stew" in line for line in after)


def test_special_characters_in_a_recipe_name_are_escaped(auth_client):
    rid = _recipe(auth_client, "Ham, Egg; Chips")["id"]
    _entry(auth_client, TODAY, recipeId=rid, mealType="dinner")
    token = _publish(auth_client)

    body = _fetch(auth_client, token).get_data(as_text=True)

    assert "SUMMARY:Dinner: Ham\\, Egg\\; Chips" in body.replace("\r\n ", "")


def test_newlines_in_notes_become_escaped_and_never_break_the_line_structure(
        auth_client):
    _entry(auth_client, TODAY, title="Stew", notes="line one\nline two")
    token = _publish(auth_client)

    body = _fetch(auth_client, token).get_data(as_text=True)

    assert "line one\\nline two" in body.replace("\r\n ", "")
    # Every physical line must still be a property or a continuation.
    for line in body.split("\r\n"):
        assert line == "" or line.startswith(" ") or ":" in line


def test_every_physical_line_stays_within_75_octets(auth_client):
    _recipe(auth_client, "Slow-roasted " + "extremely " * 20 + "long dish")
    rid = _recipe(auth_client, "Slow " + "long " * 30 + "name")["id"]
    _entry(auth_client, TODAY, recipeId=rid, notes="n" * 400)
    token = _publish(auth_client)

    body = _fetch(auth_client, token).get_data(as_text=True)

    for line in body.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, line[:80]


def test_events_are_ordered_by_date(auth_client):
    _entry(auth_client, TODAY + timedelta(days=3), title="Later")
    _entry(auth_client, TODAY, title="Sooner")
    token = _publish(auth_client)

    blocks = _events(_fetch(auth_client, token).get_data(as_text=True))

    summaries = [next(x for x in b if x.startswith("SUMMARY:")) for b in blocks]
    assert "Sooner" in summaries[0] and "Later" in summaries[1]


def test_feed_is_named_after_the_household(auth_client, gid, app):
    with app.app_context():
        from app.models import Group
        db.session.get(Group, gid).name = "The Moyses"
        db.session.commit()
    token = _publish(auth_client)

    body = _fetch(auth_client, token).get_data(as_text=True)

    assert "X-WR-CALNAME:The Moyses meal plan" in body
