"""Household food preferences: storage, the API surface, and how the AI honours
them (assistant tool + recipe drafting)."""
from app.extensions import db
from app.models import Group
from app.services.ai.agent import actions_from_trace, execute_tool
from app.services.ai.recipe_import import generate_recipe
from app.services.preferences import (
    get_preferences,
    preferences_text,
    set_preferences,
)


def _group(app):
    with app.app_context():
        g = Group(name="Household")
        db.session.add(g)
        db.session.commit()
        return g.id


# --- service ----------------------------------------------------------------

def test_get_preferences_defaults_to_empty_strings(app):
    gid = _group(app)
    with app.app_context():
        assert get_preferences(gid) == {
            "diet": "", "allergies": "", "dislikes": "", "notes": ""}


def test_set_preferences_updates_only_provided_fields(app):
    gid = _group(app)
    with app.app_context():
        set_preferences(gid, {"diet": "vegetarian", "allergies": "peanuts"})
        db.session.commit()
        set_preferences(gid, {"allergies": "shellfish"})  # diet must be untouched
        db.session.commit()
        p = get_preferences(gid)
        assert p["diet"] == "vegetarian"
        assert p["allergies"] == "shellfish"


def test_preferences_text_summarizes_only_set_fields(app):
    gid = _group(app)
    with app.app_context():
        set_preferences(gid, {"diet": "vegan", "allergies": "nuts", "dislikes": ""})
        db.session.commit()
        text = preferences_text(gid)
        assert "vegan" in text and "nuts" in text
        assert "dislikes" not in text  # empty field is omitted


def test_preferences_text_empty_when_nothing_set(app):
    gid = _group(app)
    with app.app_context():
        assert preferences_text(gid) == ""


# --- API --------------------------------------------------------------------

def test_get_preferences_returns_defaults_for_new_group(auth_client):
    body = auth_client.get("/api/v1/preferences").get_json()
    assert body == {"diet": "", "allergies": "", "dislikes": "", "notes": ""}


def test_put_preferences_persists_and_round_trips(auth_client):
    r = auth_client.put("/api/v1/preferences", json={"diet": "pescatarian"})
    assert r.status_code == 200
    assert r.get_json()["diet"] == "pescatarian"
    assert auth_client.get("/api/v1/preferences").get_json()["diet"] == "pescatarian"


def test_preferences_require_authentication(client):
    assert client.get("/api/v1/preferences").status_code == 401


def test_preferences_are_isolated_per_group(client):
    def token(email):
        client.post("/api/v1/users/register",
                    json={"email": email, "password": "password", "name": email})
        return client.post("/api/v1/users/login",
                           json={"username": email, "password": "password"}
                           ).get_json()["token"]

    a, b = token("a@a.com"), token("b@b.com")
    client.environ_base["HTTP_AUTHORIZATION"] = a
    client.put("/api/v1/preferences", json={"allergies": "gluten"})
    client.environ_base["HTTP_AUTHORIZATION"] = b
    assert client.get("/api/v1/preferences").get_json()["allergies"] == ""


# --- assistant tool ---------------------------------------------------------

def test_set_preference_tool_records_and_reads_back(app):
    gid = _group(app)
    with app.app_context():
        res = execute_tool(gid, "set_preference",
                           {"field": "diet", "value": "vegetarian"})
        assert res["preferenceSet"] == "diet"
        assert get_preferences(gid)["diet"] == "vegetarian"


def test_set_preference_tool_rejects_unknown_field(app):
    gid = _group(app)
    with app.app_context():
        assert "error" in execute_tool(gid, "set_preference",
                                       {"field": "religion", "value": "x"})


def test_set_preference_becomes_an_action_chip():
    trace = [{"tool": "set_preference", "args": {"field": "diet", "value": "vegan"},
              "result": {"preferenceSet": "diet", "value": "vegan"}}]
    actions = actions_from_trace(trace)
    assert len(actions) == 1
    assert actions[0]["kind"] == "preference"
    assert "vegan" in actions[0]["label"]


# --- recipe drafting honours preferences ------------------------------------

def test_generate_recipe_passes_preferences_into_the_prompt():
    captured = {}

    class FakeProvider:
        def complete_json(self, ask, system=None, max_tokens=None):
            captured["ask"] = ask
            return {"name": "Tofu Bowl", "recipeIngredient": ["tofu"],
                    "recipeInstructions": ["cook"]}

    generate_recipe("a quick bowl", FakeProvider(), preferences="diet: vegan")
    assert "vegan" in captured["ask"]
