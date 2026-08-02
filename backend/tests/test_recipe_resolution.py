"""Confidence-tiered recipe resolution.

Resolving a spoken name to a recipe is a guess, and acting on a wrong guess
edits or deletes the wrong food. These tests pin the policy:

* a confident match resolves to ONE recipe and the caller acts;
* an ambiguous one returns 3-5 ranked candidates and the caller acts on NOTHING;
* ranking uses tags and descriptions, not just the name, and says which it was.

The MCP tests drive the real tool functions against the real Flask app, so a
tool that acts on a low-confidence match fails here rather than in someone's
kitchen.
"""
import httpx
import pytest

import mcp_server
from app.services import recipe_resolve as rr


def _fn(tool):
    """The plain function behind an @mcp.tool()-decorated object."""
    return getattr(tool, "fn", tool)


def _add(client, name, tags=None, description="", ingredients=None, steps=None):
    body = {"name": name, "description": description}
    if tags:
        body["tags"] = tags
    if ingredients:
        body["ingredients"] = [{"display": i} for i in ingredients]
    if steps:
        body["steps"] = [{"text": s} for s in steps]
    r = client.post("/api/v1/recipes", json=body)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()


@pytest.fixture()
def mcp_api(app, monkeypatch):
    """Point the MCP server's HTTP client at the test app, authenticated."""
    client = app.test_client()
    client.post("/api/v1/users/register",
                json={"email": "r@t.com", "password": "password", "name": "R"})
    token = client.post(
        "/api/v1/users/login",
        json={"username": "r@t.com", "password": "password"},
    ).get_json()["token"]
    http = httpx.Client(
        transport=httpx.WSGITransport(app=app),
        base_url="http://testserver/api/v1",
        headers={"Authorization": token},
        timeout=15,
    )
    monkeypatch.setattr(mcp_server, "_HTTP", http)
    # A Flask test_client sharing the same auth, for seeding data.
    client.environ_base["HTTP_AUTHORIZATION"] = token
    yield client
    http.close()


# --- scoring: tags and descriptions count, and we know which matched --------

def test_exact_name_outranks_tag_which_outranks_description():
    rows = [
        {"id": "d", "name": "Stew", "tags": [], "description": "a risotto note"},
        {"id": "t", "name": "Pilaf", "tags": ["risotto"], "description": ""},
        {"id": "n", "name": "Risotto", "tags": [], "description": ""},
    ]
    ranked = rr.rank(rows, "risotto")
    assert [row["id"] for row, _, _ in ranked] == ["n", "t", "d"]
    assert [matched for _, _, matched in ranked] == ["name", "tag", "description"]


def test_a_tag_only_match_is_found_and_explained():
    rows = [{"id": "1", "name": "Weeknight Pasta", "tags": ["quick", "veg"],
             "description": ""}]
    decision = rr.decide(rows, "quick")
    assert decision["confidence"] == "high"
    assert decision["matchedOn"] == "tag"


def test_name_prefix_beats_a_pile_of_tag_matches():
    """Evidence KIND wins over evidence count — three tag hits don't outvote a name."""
    rows = [
        {"id": "1", "name": "Pasta Bake", "tags": [], "description": ""},
        {"id": "2", "name": "Ragu", "tags": ["pasta"], "description": ""},
        {"id": "3", "name": "Carbonara", "tags": ["pasta"], "description": ""},
    ]
    decision = rr.decide(rows, "pasta")
    assert decision["confidence"] == "high"
    assert decision["match"]["name"] == "Pasta Bake"


# --- the confidence decision ------------------------------------------------

def test_similar_names_are_low_confidence_with_ranked_candidates():
    rows = [
        {"id": "1", "name": "Chicken Soup", "tags": ["soup"], "description": "warming"},
        {"id": "2", "name": "Butter Chicken", "tags": ["curry"], "description": "rich"},
        {"id": "3", "name": "Chicken Pie", "tags": ["pie"], "description": ""},
    ]
    decision = rr.decide(rows, "chicken")
    assert decision["confidence"] == "low"
    assert 3 <= len(decision["candidates"]) <= rr.MAX_CANDIDATES
    first = decision["candidates"][0]
    # Candidates must carry enough for a human to choose between them.
    assert first["name"] and first["id"]
    assert "tags" in first and "description" in first and first["matchedOn"]


def test_candidates_are_capped_at_five():
    rows = [{"id": str(i), "name": f"Chicken {i}", "tags": [], "description": ""}
            for i in range(9)]
    decision = rr.decide(rows, "chicken")
    assert decision["confidence"] == "low"
    assert len(decision["candidates"]) == 5


def test_a_unique_exact_name_wins_over_longer_names():
    rows = [
        {"id": "1", "name": "Chicken Soup", "tags": [], "description": ""},
        {"id": "2", "name": "Chicken Soup Deluxe", "tags": [], "description": ""},
    ]
    decision = rr.decide(rows, "chicken soup")
    assert decision["confidence"] == "high"
    assert decision["match"]["id"] == "1"


def test_two_recipes_sharing_a_name_are_never_guessed_between():
    rows = [
        {"id": "1", "name": "Soup", "tags": ["a"], "description": ""},
        {"id": "2", "name": "soup", "tags": ["b"], "description": ""},
    ]
    assert rr.decide(rows, "Soup")["confidence"] == "low"


def test_nothing_matching_is_none():
    rows = [{"id": "1", "name": "Soup", "tags": [], "description": ""}]
    assert rr.decide(rows, "lasagne")["confidence"] == "none"


# --- /search: relevance order plus the fields needed to rank ---------------

def test_search_ranks_by_relevance_and_explains_the_match(auth_client):
    _add(auth_client, "Aaa Placeholder", description="mentions risotto")
    _add(auth_client, "Zzz Pilaf", tags=["risotto"])
    _add(auth_client, "Risotto")
    results = auth_client.get("/api/v1/search?types=recipe&q=risotto").get_json()["results"]
    # Alphabetical order would have put "Aaa Placeholder" first.
    assert [r["name"] for r in results] == ["Risotto", "Zzz Pilaf", "Aaa Placeholder"]
    assert [r["matchedOn"] for r in results] == ["name", "tag", "description"]
    assert results[1]["tags"] == ["risotto"]


# --- /recipes/resolve: the shared endpoint --------------------------------

def test_resolve_is_not_swallowed_by_the_recipe_ident_route(auth_client):
    """A recipe could otherwise be looked up as the slug 'resolve'."""
    r = auth_client.get("/api/v1/recipes/resolve?q=anything")
    assert r.status_code == 200
    assert "confidence" in r.get_json()


def test_resolve_high_for_a_unique_name(auth_client):
    _add(auth_client, "Onion Soup")
    data = auth_client.get("/api/v1/recipes/resolve?q=Onion Soup").get_json()
    assert data["confidence"] == "high"
    assert data["recipe"]["name"] == "Onion Soup"


def test_resolve_high_for_an_id_or_slug(auth_client):
    made = _add(auth_client, "Onion Soup")
    _add(auth_client, "Onion Soup Deluxe")  # would be ambiguous by name
    by_id = auth_client.get(f"/api/v1/recipes/resolve?q={made['id']}").get_json()
    assert by_id["confidence"] == "high" and by_id["matchedOn"] == "id"
    by_slug = auth_client.get(f"/api/v1/recipes/resolve?q={made['slug']}").get_json()
    assert by_slug["confidence"] == "high"


def test_resolve_low_returns_candidates_with_tags_and_description(auth_client):
    _add(auth_client, "Chicken Soup", tags=["soup"], description="warming and light")
    _add(auth_client, "Butter Chicken", tags=["curry"], description="rich and creamy")
    _add(auth_client, "Chicken Pie", tags=["pie"])
    data = auth_client.get("/api/v1/recipes/resolve?q=chicken").get_json()
    assert data["confidence"] == "low"
    assert len(data["candidates"]) == 3
    # Every candidate carries what a user needs to choose between them...
    for c in data["candidates"]:
        assert c["id"] and c["name"] and c["matchedOn"]
        assert "tags" in c and "description" in c
    # ...and the values are really populated, not just present.
    soup = next(c for c in data["candidates"] if c["name"] == "Chicken Soup")
    assert soup["tags"] == ["soup"]
    assert soup["description"] == "warming and light"


def test_resolve_none_when_nothing_matches(auth_client):
    _add(auth_client, "Onion Soup")
    data = auth_client.get("/api/v1/recipes/resolve?q=lasagne").get_json()
    assert data["confidence"] == "none"


# --- MCP: high acts, low asks ---------------------------------------------

def test_mcp_reads_act_on_a_confident_match(mcp_api):
    _add(mcp_api, "Onion Soup", ingredients=["2 onions"], steps=["Chop"])
    out = _fn(mcp_server.get_recipe)("Onion Soup")
    assert out["name"] == "Onion Soup"
    assert "needsClarification" not in out


def test_mcp_read_asks_instead_of_guessing(mcp_api):
    _add(mcp_api, "Chicken Soup", tags=["soup"])
    _add(mcp_api, "Butter Chicken", tags=["curry"])
    _add(mcp_api, "Chicken Pie", tags=["pie"])
    out = _fn(mcp_server.get_recipe)("chicken")
    assert out.get("needsClarification") is True
    assert len(out["candidates"]) == 3
    assert all(c.get("id") and c.get("name") for c in out["candidates"])


def test_mcp_edit_changes_nothing_on_a_low_match(mcp_api):
    a = _add(mcp_api, "Chicken Soup")
    b = _add(mcp_api, "Butter Chicken")
    _add(mcp_api, "Chicken Pie")
    msg = _fn(mcp_server.update_recipe)("chicken", servings=99)
    assert "matches several recipes" in msg
    for made in (a, b):
        after = mcp_api.get(f"/api/v1/recipes/{made['id']}").get_json()
        assert after["servings"] != 99


def test_mcp_plan_meal_asks_rather_than_planning_the_wrong_recipe(mcp_api):
    _add(mcp_api, "Chicken Soup")
    _add(mcp_api, "Butter Chicken")
    _add(mcp_api, "Chicken Pie")
    msg = _fn(mcp_server.plan_meal)("chicken", day="2026-01-01")
    assert "matches several recipes" in msg
    planned = mcp_api.get(
        "/api/v1/mealplans?start=2026-01-01&end=2026-01-01"
    ).get_json()["items"]
    assert planned == []


def test_mcp_delete_refuses_a_low_match_even_when_confirmed(mcp_api):
    a = _add(mcp_api, "Chicken Soup")
    b = _add(mcp_api, "Butter Chicken")
    c = _add(mcp_api, "Chicken Pie")
    msg = _fn(mcp_server.delete_recipe)("chicken", confirm=True)
    assert "matches several recipes" in msg
    for made in (a, b, c):
        assert mcp_api.get(f"/api/v1/recipes/{made['id']}").status_code == 200


def test_mcp_clarification_lists_tags_so_a_user_can_choose(mcp_api):
    _add(mcp_api, "Chicken Soup", tags=["soup", "light"])
    _add(mcp_api, "Butter Chicken", tags=["curry"])
    _add(mcp_api, "Chicken Pie", tags=["pie"])
    msg = _fn(mcp_server.update_recipe)("chicken", servings=2)
    assert "curry" in msg and "soup" in msg


def test_mcp_acts_when_given_the_id_of_one_of_several(mcp_api):
    a = _add(mcp_api, "Chicken Soup")
    _add(mcp_api, "Butter Chicken")
    _add(mcp_api, "Chicken Pie")
    msg = _fn(mcp_server.update_recipe)(a["id"], servings=6)
    assert "Updated" in msg
    assert mcp_api.get(f"/api/v1/recipes/{a['id']}").get_json()["servings"] == 6
