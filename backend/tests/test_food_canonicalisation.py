"""Saving a recipe canonicalises its ingredient names.

The problem this closes: _find_or_create_food matched on the exact lowercased
name and nothing else, so "Vietnamese Cinnamon", "cinnamon" and "Cinnamon,
ground" became three Food rows and nothing consolidated. Food.aliases existed
and was never consulted — a bypassed policy, not a missing one.
"""
from app.extensions import db
from app.models import Food, Group


def _foods(app):
    with app.app_context():
        return sorted(f.name for f in db.session.query(Food).all())


def _save(auth_client, *foods):
    return auth_client.post("/api/v1/recipes", json={
        "name": "R", "ingredients": [{"display": f, "food": f} for f in foods],
    }).get_json()


def test_a_variety_becomes_the_canonical_food_plus_a_qualifier(auth_client, app):
    body = _save(auth_client, "Vietnamese Cinnamon")
    ing = body["ingredients"][0]
    assert ing["food"]["name"] == "cinnamon"
    assert ing["qualifier"] == "vietnamese"
    assert _foods(app) == ["cinnamon"], "a 'Vietnamese Cinnamon' row was created"


def test_three_spellings_collapse_to_one_food(auth_client, app):
    _save(auth_client, "Vietnamese Cinnamon")
    _save(auth_client, "cinnamon")
    _save(auth_client, "Cinnamon, ground")
    assert _foods(app) == ["cinnamon"]


def test_a_compound_the_guards_protect_keeps_its_own_row(auth_client, app):
    """peanut butter is its own ingredient, not a variety of butter."""
    _save(auth_client, "peanut butter", "butter")
    assert _foods(app) == ["butter", "peanut butter"]


def test_an_existing_hand_curated_food_is_not_orphaned(auth_client, app):
    """A household that already has a Food called "Vietnamese cinnamon" keeps
    using it — this change must not churn rows people curated by hand."""
    with app.app_context():
        gid = db.session.query(Group).first().id
        db.session.add(Food(name="Vietnamese cinnamon", group_id=gid))
        db.session.commit()

    body = _save(auth_client, "Vietnamese cinnamon")

    assert body["ingredients"][0]["food"]["name"] == "Vietnamese cinnamon"
    # ...and nothing is split off it, or the line renders as the stutter
    # "Vietnamese cinnamon (vietnamese)".
    assert body["ingredients"][0]["qualifier"] == ""
    assert _foods(app) == ["Vietnamese cinnamon"]


def test_a_household_alias_prevents_a_duplicate(auth_client, app):
    """Food.aliases was written by the Foods API and never consulted by
    find-or-create, so an alias could not stop a duplicate being created.

    The alias here is deliberately one the shipped lexicon does NOT know
    ("nannas mince" for a household's own name). An earlier version of this test
    used eggplant/aubergine, which SEED_ALIASES already resolves in the pure
    layer — so it passed with the DB alias lookup deleted and tested nothing.
    """
    with app.app_context():
        gid = db.session.query(Group).first().id
        db.session.add(Food(name="beef mince", group_id=gid,
                            aliases='["nannas mince"]'))
        db.session.commit()

    body = _save(auth_client, "nannas mince")

    assert body["ingredients"][0]["food"]["name"] == "beef mince"
    assert _foods(app) == ["beef mince"]


def test_a_csv_alias_is_read_too(auth_client, app):
    """aliases is CSV on older rows and a JSON list on newer ones; both must be
    honoured or upgrading silently stops deduplicating."""
    with app.app_context():
        gid = db.session.query(Group).first().id
        db.session.add(Food(name="coriander", group_id=gid,
                            aliases="cilantro, chinese parsley"))
        db.session.commit()

    body = _save(auth_client, "Chinese Parsley")

    assert body["ingredients"][0]["food"]["name"] == "coriander"
    assert _foods(app) == ["coriander"]


def test_a_new_seed_food_is_stamped_with_its_material_facts(auth_client, app):
    """classification/allergens are what let a household's OWN rows take part in
    the boundary guard, rather than only the shipped seed list."""
    _save(auth_client, "whole milk")
    with app.app_context():
        milk = db.session.query(Food).filter_by(name="milk").first()
        assert milk is not None
        assert milk.classification == "dairy"
        assert "dairy" in (milk.allergens or [])


def test_an_explicit_qualifier_from_the_caller_wins(auth_client):
    """The import confirmation step sends a qualifier the human accepted; it
    must not be overwritten by the split."""
    body = auth_client.post("/api/v1/recipes", json={
        "name": "R", "ingredients": [
            {"display": "2 tsp Saigon cinnamon", "food": "Vietnamese cinnamon",
             "qualifier": "Saigon"}],
    }).get_json()
    assert body["ingredients"][0]["qualifier"] == "Saigon"


def test_an_unknown_ingredient_is_left_exactly_as_typed(auth_client, app):
    """The safe failure: not normalized, never mis-merged."""
    _save(auth_client, "quargelkase")
    assert _foods(app) == ["quargelkase"]


def test_an_existing_food_is_found_without_loading_the_whole_table(auth_client, app):
    """The exact-name query is a fast path, not behaviour.

    The fold/normalize pass below it would find the same row, so deleting the
    query changes no result — which is exactly why no behavioural test can
    protect it. What it buys is that the common case (the food already exists)
    stays one indexed lookup instead of loading every Food the household owns,
    and that IS observable. Assert the real property.
    """
    from sqlalchemy import event

    _save(auth_client, "flour", "butter", "sugar")

    seen = []

    def record(conn, cursor, statement, params, context, executemany):
        if "FROM foods" in statement:
            seen.append(" ".join(statement.split()))

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        _save(auth_client, "flour")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert seen, "no food lookup happened at all — test is not exercising it"
    # The scan to catch is the alias pass's group-only query. Queries filtering
    # on lower(name) (the fast path) or on foods.id (serializer loads by PK) are
    # both bounded and fine.
    scans = [q for q in seen
             if "lower" not in q.lower() and "foods.id = ?" not in q]
    assert not scans, f"a hit loaded the whole foods table: {scans}"
