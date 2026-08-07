"""Ranking a name against a household's own Food rows.

Ported from Edibl's services/matching.py: ranked candidates with a score and a
reason, plus a separate "resolve for a mutation" gate that refuses to guess.
Writing a food_id IS a mutation.
"""
import pytest

from app.extensions import db
from app.models import Food, Group
from app.services import food_resolve as fr


def _gid(app):
    return db.session.query(Group).first().id


def _mk(app, *names, **kw):
    gid = _gid(app)
    made = []
    for n in names:
        f = Food(name=n, group_id=gid, **kw)
        db.session.add(f)
        made.append(f)
    db.session.commit()
    return made


def test_an_exact_name_wins(auth_client, app):
    with app.app_context():
        _mk(app, "cinnamon", "cinnamon sugar")
        res = fr.index(_gid(app))("Cinnamon")
        assert res.food.name == "cinnamon"
        assert res.candidates[0].reasons == ["exact name"]


def test_an_alias_resolves_to_its_food(auth_client, app):
    with app.app_context():
        _mk(app, "aubergine", aliases="eggplant, brinjal")
        res = fr.index(_gid(app))("Eggplant")
        assert res.food.name == "aubergine"
        assert "alias" in res.candidates[0].reasons


def test_a_variety_resolves_to_the_canonical_food_and_keeps_the_qualifier(auth_client, app):
    """The motivating case: the household has `cinnamon`, the recipe says
    'Vietnamese Cinnamon'."""
    with app.app_context():
        _mk(app, "cinnamon")
        res = fr.index(_gid(app))("Vietnamese Cinnamon")
        assert res.food.name == "cinnamon"
        assert res.qualifier == "vietnamese"
        assert "variety of" in res.candidates[0].reasons


def test_a_variety_the_guards_refuse_does_not_resolve_to_the_head(auth_client, app):
    """peanut butter must not find a `butter` row — the pure layer refuses the
    split, so there is no 'variety of' evidence to carry."""
    with app.app_context():
        _mk(app, "butter")
        res = fr.index(_gid(app))("peanut butter")
        assert res.qualifier == ""
        assert not any("variety of" in c.reasons for c in res.candidates)


def test_a_short_food_name_does_not_match_everything(auth_client, app):
    """Containment needs a length floor or a two-letter row matches almost any
    query — the bug Edibl's matcher documents."""
    with app.app_context():
        _mk(app, "ox")
        assert fr.index(_gid(app))("box of oxtail soup").food is None


def test_a_shorter_query_finds_the_longer_foods(auth_client, app):
    """The direction the first version missed entirely: "pepper" has to surface
    `red pepper` and `green pepper` — which is then a question for a write."""
    with app.app_context():
        _mk(app, "red pepper", "green pepper")
        names = {c.food.name for c in fr.index(_gid(app))("pepper").candidates}
        assert names == {"red pepper", "green pepper"}


def test_the_index_is_built_once_and_memoised(auth_client, app):
    """_set_ingredients handles up to 200 rows and the shopping build touches
    every leaf ingredient of a two-week plan; a per-lookup query is an N+1."""
    with app.app_context():
        _mk(app, "cinnamon", "salt", "flour")
        from sqlalchemy import event

        queries = []
        engine = db.session.get_bind()
        hook = lambda *a, **k: queries.append(1)  # noqa: E731
        event.listen(engine, "before_cursor_execute", hook)
        try:
            match = fr.index(_gid(app))
            after_build = len(queries)
            for _ in range(200):
                match("Vietnamese cinnamon")
                match("sea salt")
        finally:
            event.remove(engine, "before_cursor_execute", hook)

        assert len(queries) == after_build, "lookups after the index was built hit the DB"


def test_repeated_lookups_are_memoised(auth_client, app, monkeypatch):
    """Distinct from the query test above: loading the foods once prevents the
    N+1; this cache saves the RANKING work, which is every food against the seed
    vocabulary."""
    with app.app_context():
        _mk(app, "cinnamon", "salt")
        calls = []
        real = fr._rank
        monkeypatch.setattr(fr, "_rank", lambda foods, raw: (calls.append(raw), real(foods, raw))[1])

        match = fr.index(_gid(app))
        for _ in range(50):
            match("Vietnamese cinnamon")
        assert len(calls) == 1, f"ranked {len(calls)} times for one distinct name"


# --- the mutation gate -------------------------------------------------------

def test_a_clear_winner_resolves_for_a_write(auth_client, app):
    with app.app_context():
        _mk(app, "cinnamon", "nutmeg")
        res = fr.resolve_for_mutation(fr.index(_gid(app)), "cinnamon")
        assert res.food.name == "cinnamon" and not res.ambiguous


def test_a_near_tie_refuses_to_guess(auth_client, app):
    """Two foods matching on the same weak evidence is a question, not an
    answer — the caller leaves food_id NULL and shows the candidates."""
    with app.app_context():
        _mk(app, "red pepper", "green pepper")
        res = fr.resolve_for_mutation(fr.index(_gid(app)), "pepper")
        assert res.food is None
        assert res.ambiguous
        assert len(res.candidates) >= 2


def test_nothing_found_is_not_ambiguous(auth_client, app):
    with app.app_context():
        _mk(app, "cinnamon")
        res = fr.resolve_for_mutation(fr.index(_gid(app)), "quinoa")
        assert res.food is None and not res.ambiguous


def test_a_lone_weak_hit_still_resolves(auth_client, app):
    """Matching one food's description is enough when it is the only thing
    found — that is what makes a vague name usable at all."""
    with app.app_context():
        _mk(app, "cinnamon", description="warm baking spice from bark")
        res = fr.resolve_for_mutation(fr.index(_gid(app)), "bark")
        assert res.food is not None and not res.ambiguous


@pytest.mark.parametrize("stored,queried", [
    ("eggplant, brinjal", "brinjal"),
    ('["eggplant", "brinjal"]', "brinjal"),
])
def test_aliases_are_read_the_same_whatever_they_are_stored_as(auth_client, app, stored, queried):
    """CSV today, a JSON list after migration 0013. Three call sites each split
    this string their own way; aliases_of is the only place that knows."""
    with app.app_context():
        _mk(app, "aubergine", aliases=stored)
        assert fr.index(_gid(app))(queried).food.name == "aubergine"
