"""Proposals for consolidating duplicate Food rows.

Deliberately a proposal, not a migration. A migration rewriting food_id cannot
be reversed without an audit table recording every touched row, this add-on
auto-upgrades so it would land unattended, and it would contradict the
propose-then-confirm pattern the rest of the app uses. Reversibility then comes
free: the user saw it and chose it.
"""
from app.extensions import db
from app.models import Food


def _food(client, name, **kw):
    return client.post("/api/v1/foods", json={"name": name, **kw}).get_json()


def _proposals(client):
    r = client.get("/api/v1/foods/duplicates")
    assert r.status_code == 200
    return r.get_json()


def _by_canonical(proposals):
    return {p["canonical"]: p for p in proposals}


def test_two_spellings_of_one_food_are_proposed(auth_client):
    _food(auth_client, "cinnamon")
    _food(auth_client, "Vietnamese Cinnamon")

    props = _proposals(auth_client)

    assert len(props) == 1
    p = props[0]
    assert p["canonical"] == "cinnamon"
    # The canonical row is the one to keep; the variety is what folds into it.
    assert p["keep"]["name"] == "cinnamon"
    assert [f["name"] for f in p["merge"]] == ["Vietnamese Cinnamon"]


def test_nothing_is_proposed_when_there_are_no_duplicates(auth_client):
    _food(auth_client, "cinnamon")
    _food(auth_client, "nutmeg")
    assert _proposals(auth_client) == []


def test_materially_different_foods_are_never_proposed(auth_client):
    """The whole safety property, at the proposal layer too."""
    for name in ["butter", "peanut butter", "milk", "almond milk",
                 "rice", "rice vinegar"]:
        _food(auth_client, name)
    assert _proposals(auth_client) == []


def test_a_proposal_says_how_much_is_affected(auth_client):
    """You cannot judge a merge without knowing what it touches."""
    keep = _food(auth_client, "cinnamon")
    drop = _food(auth_client, "Vietnamese Cinnamon")
    auth_client.post("/api/v1/recipes", json={"name": "Buns", "ingredients": [
        {"display": "2 tsp", "foodId": drop["id"]}]})
    auth_client.post("/api/v1/recipes", json={"name": "Cake", "ingredients": [
        {"display": "1 tsp", "foodId": keep["id"]}]})

    p = _proposals(auth_client)[0]

    assert p["merge"][0]["usageCount"] == 1
    assert p["keep"]["usageCount"] == 1


def test_the_most_used_row_is_kept_when_neither_is_canonical(auth_client):
    """With no exact-canonical row, keeping the one already used most is the
    change that touches least.

    The most-used row is deliberately the alphabetically LATER one: with
    "Ceylon" winning on both counts this passed with the ranking deleted.
    """
    rare = _food(auth_client, "Ceylon Cinnamon")
    common = _food(auth_client, "Vietnamese Cinnamon")
    for i in range(3):
        auth_client.post("/api/v1/recipes", json={"name": f"R{i}", "ingredients": [
            {"display": "x", "foodId": common["id"]}]})
    auth_client.post("/api/v1/recipes", json={"name": "Solo", "ingredients": [
        {"display": "x", "foodId": rare["id"]}]})

    p = _proposals(auth_client)[0]

    assert p["keep"]["name"] == "Vietnamese Cinnamon"
    assert [f["name"] for f in p["merge"]] == ["Ceylon Cinnamon"]


def test_the_canonical_row_is_kept_even_when_it_is_used_less(auth_client):
    """Canonical outranks usage, and "salt" sorts AFTER "Maldon salt" — so this
    fails if either the canonical clause or the ordering is dropped."""
    canonical = _food(auth_client, "salt")
    variety = _food(auth_client, "Maldon salt")
    for i in range(3):
        auth_client.post("/api/v1/recipes", json={"name": f"S{i}", "ingredients": [
            {"display": "x", "foodId": variety["id"]}]})
    auth_client.post("/api/v1/recipes", json={"name": "Solo", "ingredients": [
        {"display": "x", "foodId": canonical["id"]}]})

    p = _proposals(auth_client)[0]

    assert p["keep"]["name"] == "salt"
    assert [f["name"] for f in p["merge"]] == ["Maldon salt"]


def test_proposals_are_scoped_to_the_household(auth_client):
    _food(auth_client, "cinnamon")
    _food(auth_client, "Vietnamese Cinnamon")

    auth_client.post("/api/v1/users/register",
                     json={"email": "b@b.com", "password": "password", "name": "B"})
    token = auth_client.post("/api/v1/users/login",
                             json={"username": "b@b.com", "password": "password"}
                             ).get_json()["token"]
    auth_client.environ_base["HTTP_AUTHORIZATION"] = token

    assert _proposals(auth_client) == [], "another household's duplicates leaked"


def test_a_proposal_changes_nothing_on_its_own(auth_client, app):
    _food(auth_client, "cinnamon")
    drop = _food(auth_client, "Vietnamese Cinnamon")

    _proposals(auth_client)

    with app.app_context():
        assert db.session.get(Food, drop["id"]) is not None
        assert db.session.query(Food).count() == 2


def test_a_proposal_is_executed_by_the_existing_merge_endpoint(auth_client, app):
    """No second write path: the proposal names ids the merge endpoint accepts,
    so the confirmation and the guards are the ones already tested."""
    _food(auth_client, "cinnamon")
    _food(auth_client, "Vietnamese Cinnamon")
    p = _proposals(auth_client)[0]

    r = auth_client.post(f"/api/v1/foods/{p['keep']['id']}/merge",
                         json={"fromId": p["merge"][0]["id"], "confirm": True})

    assert r.status_code == 200
    assert _proposals(auth_client) == []
    with app.app_context():
        assert db.session.query(Food).count() == 1
