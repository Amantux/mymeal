"""The alias write policy: one folded key resolves to one food per household.

Storage becoming JSON killed forged rows and the overflow, but the invariants
that actually protect resolution are write policy, proven live before this
existed:

* ``fold(normalize_text("salt, kosher"))`` is ``"salt"`` — so even stored as a
  single JSON alias, the RESOLVER answers "salt" -> cinnamon. The old
  regression test asserted the stored list and passed while the resolver
  stayed wrong.
* ``_existing_match`` compared raw lowercase while ``_rank`` compared folded
  keys — two disagreeing definitions of "the same name", so ``POST /foods
  {"name": "Cilantros"}`` created a duplicate the resolver then treated as the
  same food.
* ``PUT /foods`` accepted any alias list with no cross-food check, so two
  foods could both claim "cilantro" and resolution was nondeterministic on
  exactly the data meant to disambiguate it.
"""
import pytest

from app.extensions import db
from app.models import Food
from app.services import food_resolve


def _food(client, name, **kw):
    return client.post("/api/v1/foods", json={"name": name, **kw}).get_json()


def _stored(app, food_id):
    with app.app_context():
        return db.session.get(Food, food_id).aliases


# --- the resolver consequence, not just the stored list ----------------------

def test_merging_a_comma_name_does_not_teach_the_resolver_a_wrong_word(auth_client, app):
    keep = _food(auth_client, "cinnamon")
    drop = _food(auth_client, "salt, kosher")

    auth_client.post(f"/api/v1/foods/{keep['id']}/merge",
                     json={"fromId": drop["id"], "confirm": True})

    with app.app_context():
        gid = db.session.query(Food).first().group_id
        resolution = food_resolve.index(gid)("salt")
    hit = resolution.food.name if resolution.food else None
    assert hit != "cinnamon", (
        "the resolver now answers 'salt' with cinnamon — the stored-list "
        "assertion missed exactly this")


# --- one key, one food -------------------------------------------------------

def test_two_foods_cannot_both_claim_an_alias(auth_client, app):
    """The alias is deliberately a HOUSEHOLD word the shipped lexicon has
    never heard of: a lexicon-known term ("cilantro") is refused by the
    seed-food guard as well, which made the first version of this test pass
    with the claim check deleted — it was testing the wrong guard."""
    a = _food(auth_client, "coriander", aliases=["nannas green stuff"])
    b = _food(auth_client, "parsley")

    auth_client.put(f"/api/v1/foods/{b['id']}",
                    json={"aliases": ["nannas green stuff"]})

    assert _stored(app, a["id"]) == ["nannas green stuff"]
    assert _stored(app, b["id"]) == [], \
        "the second food was allowed to claim an alias already taken"


def test_an_alias_equal_to_another_foods_name_is_refused(auth_client, app):
    _food(auth_client, "salt")
    b = _food(auth_client, "cinnamon")

    auth_client.put(f"/api/v1/foods/{b['id']}", json={"aliases": ["Salt"]})

    assert _stored(app, b["id"]) == []


def test_a_food_keeps_its_own_aliases_on_update(auth_client, app):
    """The claim index must not refuse a food its OWN existing aliases."""
    a = _food(auth_client, "coriander", aliases=["cilantro"])

    auth_client.put(f"/api/v1/foods/{a['id']}",
                    json={"aliases": ["cilantro", "chinese parsley"]})

    assert _stored(app, a["id"]) == ["cilantro", "chinese parsley"]


def test_an_alias_equal_to_the_foods_own_name_is_dropped(auth_client, app):
    a = _food(auth_client, "coriander", aliases=["Coriander", "cilantro"])
    assert _stored(app, a["id"]) == ["cilantro"]


# --- one equivalence rule ----------------------------------------------------

def test_create_matches_an_existing_food_by_folded_key(auth_client):
    """"Cilantros" folds to "cilantro"; raw-lowercase matching missed it and
    created a duplicate the resolver then treated as the same food."""
    first = _food(auth_client, "cilantro")
    again = auth_client.post("/api/v1/foods", json={"name": "Cilantros"})

    assert again.get_json()["id"] == first["id"]
    assert len(auth_client.get("/api/v1/foods").get_json()) == 1


def test_a_variety_is_still_creatable_as_its_own_food(auth_client):
    """The folded key does NOT canonicalise: "Vietnamese cinnamon" must not
    collapse into "cinnamon" on explicit creation — that is what the merge
    confirmation is for."""
    _food(auth_client, "cinnamon")
    r = auth_client.post("/api/v1/foods", json={"name": "Vietnamese cinnamon"})
    assert r.status_code == 201
    assert len(auth_client.get("/api/v1/foods").get_json()) == 2


def test_existing_match_is_deterministic_across_requests(auth_client, app):
    """Legacy data may already contain a collision (two foods claiming one
    alias). The lookup must return the same winner every time, not whatever
    row iteration order happens to yield."""
    a = _food(auth_client, "coriander")
    b = _food(auth_client, "culantro")
    with app.app_context():
        # Bypass the API guard to plant the collision, as legacy data would.
        for fid in (a["id"], b["id"]):
            db.session.get(Food, fid).aliases = ["cilantro"]
        db.session.commit()

    winners = {auth_client.post("/api/v1/foods",
                                json={"name": "cilantro"}).get_json()["id"]
               for _ in range(5)}
    assert len(winners) == 1
    # And the winner is the SPECIFIED one — earliest created — not merely a
    # stable accident. (SQLite returns insertion order even for unordered
    # queries, so "same winner five times" alone cannot detect a missing
    # ORDER BY; pinning the semantic at least catches a wrong tie-break rule.)
    assert winners == {a["id"]}


# --- growth and storage ------------------------------------------------------

def test_every_merged_name_survives_up_to_the_count_cap(auth_client, app):
    """The old byte cap silently dropped later merges' names, so a re-import
    recreated exactly the row that was merged away."""
    keep = _food(auth_client, "base")
    names = [f"a-fairly-long-food-name-number-{i:02d}" for i in range(30)]
    for name in names:
        drop = _food(auth_client, name)
        auth_client.post(f"/api/v1/foods/{keep['id']}/merge",
                         json={"fromId": drop["id"], "confirm": True})

    stored = _stored(app, keep["id"])
    assert isinstance(stored, list)
    assert set(names) <= set(stored), "a merged name was silently dropped"
    assert len(stored) <= food_resolve.MAX_ALIASES


def test_the_count_cap_is_enforced(auth_client, app):
    food = _food(auth_client, "base",
                 aliases=[f"alias-{i}" for i in range(200)])
    stored = _stored(app, food["id"])
    assert len(stored) == food_resolve.MAX_ALIASES


def test_an_alias_with_a_comma_keeps_its_identity_part(auth_client, app):
    """"salt, kosher" cannot be stored verbatim: normalize_text truncates at
    the comma, so the resolver could only ever see "salt" — storing text the
    resolver silently discards is how the original bug hid. The identity part
    is kept; the boundary guard then applies to it like any other term."""
    a = _food(auth_client, "sea salt", aliases=["fleur de sel, coarse"])
    assert _stored(app, a["id"]) == ["fleur de sel"]


# --- wire format -------------------------------------------------------------

def test_the_wire_shape_is_a_clean_list_for_every_storage_form(auth_client, app):
    """serializers.food_out had its own CSV split, which mangled JSON-string
    rows onto the wire as ['["eggplant"', '"brinjal"]']. Legacy forms must
    serialise identically."""
    a = _food(auth_client, "aubergine")
    with app.app_context():
        food = db.session.get(Food, a["id"])
        food.aliases = ["eggplant", "brinjal"]
        db.session.commit()

    out = [f for f in auth_client.get("/api/v1/foods").get_json()
           if f["id"] == a["id"]][0]
    assert out["aliases"] == ["eggplant", "brinjal"]


@pytest.mark.parametrize("q, expect_hit", [
    ("eggplant", True),    # alias substring
    ("brinj", True),       # partial alias
    ("zucchini", False),
])
def test_lookup_still_searches_aliases_after_the_type_change(auth_client, app, q, expect_hit):
    """lookup used ILIKE on the raw column; on Postgres a json column has no
    ILIKE operator. The cast path must keep alias search working."""
    a = _food(auth_client, "aubergine", aliases=["eggplant", "brinjal"])
    body = auth_client.get(f"/api/v1/search?q={q}&types=food").get_json()
    hits = [i for i in body["results"]
            if i.get("type") == "food" and i["id"] == a["id"]]
    assert bool(hits) == expect_hit
