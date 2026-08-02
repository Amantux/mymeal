"""MCP recipe CRUD + version/experiment tools.

These drive the real tool functions against the real Flask app (httpx WSGI
transport), so a wrapper that sends the wrong payload shape fails here rather
than only in production. FastMCP wraps each tool, so call `.fn`.
"""
import httpx
import pytest

import mcp_server


def _fn(tool):
    """The plain function behind an @mcp.tool()-decorated object."""
    return getattr(tool, "fn", tool)


@pytest.fixture()
def mcp_api(app, monkeypatch):
    """Point the MCP server's HTTP client at the test app, authenticated."""
    client = app.test_client()
    client.post("/api/v1/users/register",
                json={"email": "m@t.com", "password": "password", "name": "M"})
    token = client.post(
        "/api/v1/users/login",
        json={"username": "m@t.com", "password": "password"},
    ).get_json()["token"]

    http = httpx.Client(
        transport=httpx.WSGITransport(app=app),
        base_url="http://testserver/api/v1",
        headers={"Authorization": token},
        timeout=15,
    )
    monkeypatch.setattr(mcp_server, "_HTTP", http)
    yield http
    http.close()


def _make_recipe(name="Onion Soup", ingredients=None, steps=None, servings=4):
    return _fn(mcp_server.add_recipe)(
        name=name,
        ingredients=ingredients or ["2 onions", "1 l stock"],
        steps=steps or ["Chop", "Simmer"],
        servings=servings,
    )


# --- create / read ----------------------------------------------------------

def test_add_then_get_recipe(mcp_api):
    assert "Onion Soup" in _make_recipe()
    got = _fn(mcp_server.get_recipe)("Onion Soup")
    assert got["name"] == "Onion Soup"
    assert got["ingredients"] == ["2 onions", "1 l stock"]
    assert got["steps"] == ["Chop", "Simmer"]


# --- update -----------------------------------------------------------------

def test_update_recipe_changes_only_what_is_passed(mcp_api):
    _make_recipe()
    out = _fn(mcp_server.update_recipe)("Onion Soup", servings=8)
    assert "Updated" in out

    got = _fn(mcp_server.get_recipe)("Onion Soup")
    assert got["servings"] == 8
    # The whole point of the omit-to-keep contract: ingredients/steps survive.
    assert got["ingredients"] == ["2 onions", "1 l stock"]
    assert got["steps"] == ["Chop", "Simmer"]


def test_update_recipe_replaces_ingredients_when_supplied(mcp_api):
    _make_recipe()
    _fn(mcp_server.update_recipe)("Onion Soup", ingredients=["3 onions"])
    got = _fn(mcp_server.get_recipe)("Onion Soup")
    assert got["ingredients"] == ["3 onions"]
    assert got["steps"] == ["Chop", "Simmer"]   # untouched


def test_update_recipe_rename(mcp_api):
    _make_recipe()
    _fn(mcp_server.update_recipe)("Onion Soup", name="French Onion Soup")
    assert _fn(mcp_server.get_recipe)("French Onion Soup")["name"] == "French Onion Soup"


def test_update_recipe_with_no_fields_is_a_no_op(mcp_api):
    _make_recipe()
    assert "Nothing to update" in _fn(mcp_server.update_recipe)("Onion Soup")


def test_update_recipe_unknown_name_returns_message(mcp_api):
    assert "No recipe matching" in _fn(mcp_server.update_recipe)("Nope", servings=2)


# --- delete -----------------------------------------------------------------

def test_delete_recipe(mcp_api):
    _make_recipe()
    assert "Deleted" in _fn(mcp_server.delete_recipe)("Onion Soup", confirm=True)
    assert "error" in _fn(mcp_server.get_recipe)("Onion Soup")


def test_delete_recipe_unknown_name_returns_message(mcp_api):
    assert "No recipe matching" in _fn(mcp_server.delete_recipe)("Nope")


def test_delete_without_confirm_previews_and_keeps_the_recipe(mcp_api):
    """Step 1 of the two-step flow: describe the loss, destroy nothing."""
    _make_recipe()
    out = _fn(mcp_server.delete_recipe)("Onion Soup")

    assert "confirm=true" in out
    assert "2 ingredient(s)" in out and "2 step(s)" in out
    # Still there — the preview must not have deleted anything.
    assert _fn(mcp_server.get_recipe)("Onion Soup")["name"] == "Onion Soup"


def test_delete_preview_counts_the_versions_that_would_be_lost(mcp_api):
    _make_recipe()
    _fn(mcp_server.update_recipe)("Onion Soup", servings=6)      # -> auto history
    _fn(mcp_server.start_recipe_experiment)("Onion Soup", "More thyme")

    n = len(_fn(mcp_server.list_recipe_versions)("Onion Soup")["versions"])
    assert n >= 2
    assert f"{n} saved version(s)" in _fn(mcp_server.delete_recipe)("Onion Soup")


def test_delete_refuses_an_ambiguous_name_even_with_confirm(mcp_api):
    """The dangerous case: a fuzzy voice phrase matching several recipes."""
    _make_recipe(name="Chicken Soup")
    _make_recipe(name="Chicken Pie")

    out = _fn(mcp_server.delete_recipe)("Chicken", confirm=True)
    assert "matches several recipes" in out and "Nothing was changed" in out
    # Both survive — an ambiguous delete must never pick one.
    assert _fn(mcp_server.get_recipe)("Chicken Soup")["name"] == "Chicken Soup"
    assert _fn(mcp_server.get_recipe)("Chicken Pie")["name"] == "Chicken Pie"


def test_delete_accepts_an_exact_name_that_is_a_prefix_of_another(mcp_api):
    """'Chicken Soup' is unambiguous even though 'Chicken Soup Deluxe' matches too."""
    _make_recipe(name="Chicken Soup")
    _make_recipe(name="Chicken Soup Deluxe")

    assert "Deleted" in _fn(mcp_server.delete_recipe)("Chicken Soup", confirm=True)
    assert _fn(mcp_server.get_recipe)("Chicken Soup Deluxe")["name"] == "Chicken Soup Deluxe"


def test_delete_by_id_is_never_ambiguous(mcp_api):
    _make_recipe(name="Chicken Soup")
    _make_recipe(name="Chicken Pie")
    rid = _fn(mcp_server.search_recipes)("Chicken Pie")[0]["id"]

    assert "Deleted" in _fn(mcp_server.delete_recipe)(rid, confirm=True)


# --- versions & experiments -------------------------------------------------

def _new_experiment(label="More thyme"):
    out = _fn(mcp_server.start_recipe_experiment)("Onion Soup", label)
    vid = out.rsplit("version id ", 1)[1].rstrip(").")
    return vid


def test_edit_creates_auto_history(mcp_api):
    _make_recipe()
    _fn(mcp_server.update_recipe)("Onion Soup", servings=6)
    versions = _fn(mcp_server.list_recipe_versions)("Onion Soup")["versions"]
    assert any(v["kind"] == "auto" for v in versions)


def test_experiment_lifecycle(mcp_api):
    """start → list → edit (live untouched) → feedback → promote."""
    _make_recipe()
    vid = _new_experiment()

    listed = _fn(mcp_server.list_recipe_versions)("Onion Soup")["versions"]
    exp = [v for v in listed if v["id"] == vid]
    assert exp and exp[0]["kind"] == "experiment" and exp[0]["status"] == "open"

    out = _fn(mcp_server.update_experiment)(
        "Onion Soup", vid, ingredients=["2 onions", "1 l stock", "thyme"])
    assert "unchanged" in out
    # The live recipe must NOT have moved.
    assert _fn(mcp_server.get_recipe)("Onion Soup")["ingredients"] == [
        "2 onions", "1 l stock"]

    assert "Recorded feedback" in _fn(mcp_server.add_experiment_feedback)(
        "Onion Soup", vid, rating=5, feedback="Better with thyme")
    rated = [v for v in _fn(mcp_server.list_recipe_versions)("Onion Soup")["versions"]
             if v["id"] == vid][0]
    assert rated["rating"] == 5

    assert "Promoted" in _fn(mcp_server.promote_experiment)("Onion Soup", vid)
    assert _fn(mcp_server.get_recipe)("Onion Soup")["ingredients"] == [
        "2 onions", "1 l stock", "thyme"]


def test_update_experiment_preserves_unpassed_snapshot_fields(mcp_api):
    """A partial edit must MERGE into the stored snapshot, not replace it.

    Asserted on the snapshot itself, not on the live recipe after a promote:
    `_apply` ignores absent keys, so a gutted snapshot would still leave the live
    recipe looking correct and the test would prove nothing.
    """
    _make_recipe()
    rid = mcp_server._resolve("Onion Soup")[0]["id"]
    vid = _new_experiment()

    _fn(mcp_server.update_experiment)("Onion Soup", vid, steps=["Chop", "Simmer", "Serve"])

    snap = mcp_server._get(f"/recipes/{rid}/versions/{vid}")["snapshot"]
    assert [s["text"] for s in snap["steps"]] == ["Chop", "Simmer", "Serve"]
    # The fields the caller did NOT pass must still be in the snapshot.
    assert [i["display"] for i in snap["ingredients"]] == ["2 onions", "1 l stock"]
    assert snap["servings"] == 4
    assert snap["name"] == "Onion Soup"

    # And promoting it yields exactly that content on the live recipe.
    _fn(mcp_server.promote_experiment)("Onion Soup", vid)
    got = _fn(mcp_server.get_recipe)("Onion Soup")
    assert got["steps"] == ["Chop", "Simmer", "Serve"]
    assert got["ingredients"] == ["2 onions", "1 l stock"]
    assert got["servings"] == 4


def test_restore_round_trips(mcp_api):
    _make_recipe()
    _fn(mcp_server.update_recipe)("Onion Soup", servings=9)
    auto = [v for v in _fn(mcp_server.list_recipe_versions)("Onion Soup")["versions"]
            if v["kind"] == "auto"][0]
    assert "Restored" in _fn(mcp_server.restore_recipe_version)("Onion Soup", auto["id"])
    assert _fn(mcp_server.get_recipe)("Onion Soup")["servings"] == 4


def test_discard_experiment(mcp_api):
    _make_recipe()
    vid = _new_experiment()
    assert "Discarded" in _fn(mcp_server.discard_experiment)(
        "Onion Soup", vid, confirm=True)
    ids = [v["id"] for v in _fn(mcp_server.list_recipe_versions)("Onion Soup")["versions"]]
    assert vid not in ids


def test_discard_without_confirm_previews_and_keeps_the_version(mcp_api):
    _make_recipe()
    vid = _new_experiment("More thyme")

    out = _fn(mcp_server.discard_experiment)("Onion Soup", vid)
    assert "confirm=true" in out
    assert "More thyme" in out          # names the experiment being discarded

    ids = [v["id"] for v in _fn(mcp_server.list_recipe_versions)("Onion Soup")["versions"]]
    assert vid in ids                   # preview destroyed nothing


def test_version_tools_report_unknown_version(mcp_api):
    _make_recipe()
    bogus = "no-such-version-id"
    assert "No version" in _fn(mcp_server.update_experiment)("Onion Soup", bogus, servings=2)
    assert "No version" in _fn(mcp_server.add_experiment_feedback)(
        "Onion Soup", bogus, rating=3)
    assert "No version" in _fn(mcp_server.promote_experiment)("Onion Soup", bogus)
    assert "No version" in _fn(mcp_server.restore_recipe_version)("Onion Soup", bogus)
    assert "No version" in _fn(mcp_server.discard_experiment)("Onion Soup", bogus)


def test_version_tools_report_unknown_recipe(mcp_api):
    assert "error" in _fn(mcp_server.list_recipe_versions)("Nope")
    assert "No recipe matching" in _fn(mcp_server.start_recipe_experiment)("Nope", "x")
    assert "No recipe matching" in _fn(mcp_server.promote_experiment)("Nope", "v")


# --- read-only key safety ---------------------------------------------------

def test_only_the_read_tool_is_allowlisted():
    """A Read-Only API key may list versions but must not mutate anything.

    READ_TOOLS is fail-safe (absent == write == denied), so this pins the intent:
    every new tool except list_recipe_versions stays out of the allowlist.
    """
    assert "list_recipe_versions" in mcp_server.READ_TOOLS
    for write_tool in (
        "update_recipe", "delete_recipe", "start_recipe_experiment",
        "update_experiment", "add_experiment_feedback", "promote_experiment",
        "restore_recipe_version", "discard_experiment",
    ):
        assert write_tool not in mcp_server.READ_TOOLS


def test_guard_denies_write_tools_for_a_read_only_key():
    """The body screen a read-only key passes through must reject the new writes."""
    import json as _json

    def call(tool):
        body = _json.dumps(
            {"method": "tools/call", "params": {"name": tool}}).encode()
        return mcp_server._is_tools_call_body(body)   # True == "deny for read key"

    assert call("list_recipe_versions") is False      # allowed
    for tool in ("update_recipe", "delete_recipe", "promote_experiment",
                 "discard_experiment", "update_experiment"):
        assert call(tool) is True                     # denied
