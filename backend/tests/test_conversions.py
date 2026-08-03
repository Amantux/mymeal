"""Learned unit→weight conversions.

The risk this feature carries is that a number found on the internet ends up
presented as though the app shipped with it. So the tests that matter are the
ones about precedence and trust: physics wins, unreviewed values are not used,
and a lookup happens once.
"""
import pytest

from app.extensions import db
from app.models.unit_conversion import UnitConversion
from app.services import conversions, units


class Search:
    """Stands in for websearch: counts calls, returns canned results."""

    def __init__(self, results, key=True):
        self.results = results
        self.key = key
        self.calls = 0

    def enabled(self, settings=None):
        return self.key

    def web_search(self, query, max_results=3, settings=None):
        self.calls += 1
        self.last_query = query
        return self.results


def hit(grams, url="https://example.com/a"):
    return {"title": "Butter conversions", "content": f"One stick is {grams} g.",
            "url": url}


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield


@pytest.fixture()
def patched(monkeypatch):
    def apply(search):
        monkeypatch.setattr(conversions, "websearch", search)
        return search
    return apply


# --- food_term: the cache key -------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("butter", "butter"),
    ("unsalted butter, softened", "butter"),
    ("butter (softened)", "butter"),
    ("large free-range eggs", "eggs"),
    ("finely chopped garlic", "garlic"),
    ("", ""),
])
def test_the_cache_key_ignores_preparation(text, expected):
    """If preparation words split the key, the cache never hits and every
    recipe pays for another lookup."""
    assert conversions.food_term(text) == expected


# --- parse_grams --------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("One stick is 113 g.", 113.0),
    ("113g of butter", 113.0),
    ("about 400 grams", 400.0),
    ("a stick weighs 4 ounces", None),      # not grams; we do not infer
    ("the pack is 0 g", None),              # below MIN_GRAMS
    ("a sack of 50000 g", None),            # above MAX_GRAMS
    ("no numbers here at all", None),
    ("", None),
])
def test_a_snippet_yields_a_number_only_when_it_states_one_in_grams(text, expected):
    assert conversions.parse_grams(text) == expected


def test_a_result_with_no_parsable_number_is_discarded_not_stored_as_zero(ctx, patched):
    """Storing 0 g would silently make every future weight wrong."""
    search = patched(Search([{"title": "Butter", "content": "It depends!", "url": "u"}]))

    assert conversions.learn("stick", "butter", None) is None
    assert db.session.query(UnitConversion).count() == 0
    assert search.calls == 1


# --- precedence ---------------------------------------------------------------

def test_a_shipped_density_always_beats_a_learned_value(ctx):
    """A number from the web must never override physics the app ships with."""
    db.session.add(UnitConversion(
        unit="cup", food_term="flour", grams_per_unit=999.0,
        source="web", confidence=1.0, status="confirmed", group_id=None,
    ))
    db.session.flush()

    grams = units.to_grams("1 cup flour", learned=conversions.resolver(None))

    # 236.588 ml * 0.53 g/ml from the built-in density table, not 999.
    assert grams == pytest.approx(125.4, abs=1.0)


def test_a_learned_value_answers_a_unit_the_tables_cannot(ctx):
    db.session.add(UnitConversion(
        unit="stick", food_term="butter", grams_per_unit=113.0,
        source="web", confidence=0.95, status="confirmed", group_id=None,
    ))
    db.session.flush()

    assert units.to_grams("2 sticks butter") is None      # without the resolver
    assert units.to_grams(
        "2 sticks butter", learned=conversions.resolver(None)
    ) == pytest.approx(226.0)


def test_a_pending_conversion_is_never_used_in_a_calculation(ctx):
    """Unreviewed is not the same as unknown-but-usable — it must behave as if
    the row does not exist until a human accepts it."""
    db.session.add(UnitConversion(
        unit="stick", food_term="butter", grams_per_unit=113.0,
        source="web", confidence=0.5, status="pending", group_id=None,
    ))
    db.session.flush()

    assert units.to_grams(
        "2 sticks butter", learned=conversions.resolver(None)
    ) is None


def test_another_households_conversion_is_not_visible(ctx):
    from app.models.group import Group
    other = Group(name="Next door")
    db.session.add(other)
    db.session.flush()
    db.session.add(UnitConversion(
        unit="stick", food_term="butter", grams_per_unit=113.0,
        source="user", confidence=1.0, status="confirmed", group_id=other.id,
    ))
    db.session.flush()

    assert units.to_grams(
        "2 sticks butter", learned=conversions.resolver(None)
    ) is None


def test_a_failing_resolver_does_not_break_a_render(ctx):
    """to_grams runs on read paths; a broken lookup must degrade to 'no weight',
    never to a 500."""
    def boom(unit, food):
        raise RuntimeError("database gone")

    assert units.to_grams("2 sticks butter", learned=boom) is None


# --- learning -----------------------------------------------------------------

def test_a_lookup_happens_once_and_the_second_call_hits_the_cache(ctx, patched):
    search = patched(Search([hit(113), hit(113, "https://example.com/b")]))

    first = conversions.learn("stick", "butter", None)
    second = conversions.learn("sticks", "unsalted butter, softened", None)

    assert search.calls == 1, "the second call must not reach the web"
    assert first.id == second.id
    assert db.session.query(UnitConversion).count() == 1


def test_agreement_across_sources_raises_confidence_to_confirmed(ctx, patched):
    patched(Search([hit(113), hit(115, "https://example.com/b")]))

    row = conversions.learn("stick", "butter", None)

    assert row.status == "confirmed"
    assert row.source == "web"
    assert row.source_url == "https://example.com/a", "provenance must be recorded"


def test_a_lone_unsupported_answer_is_stored_for_review_not_used(ctx, patched):
    patched(Search([hit(113)]))

    row = conversions.learn("stick", "butter", None)

    assert row.status == "pending"
    assert units.to_grams(
        "1 stick butter", learned=conversions.resolver(None)
    ) is None


def test_no_search_key_means_no_lookup_and_no_row(ctx, patched):
    """The whole feature is optional; without a key the app behaves as before."""
    search = patched(Search([hit(113)], key=False))

    assert conversions.learn("stick", "butter", None) is None
    assert search.calls == 0
    assert db.session.query(UnitConversion).count() == 0


def test_an_unknown_unit_is_never_looked_up(ctx, patched):
    search = patched(Search([hit(113)]))

    assert conversions.learn("smidgen", "butter", None) is None
    assert search.calls == 0


# --- learn_for_lines ----------------------------------------------------------

def test_lines_the_app_can_already_weigh_cost_nothing(ctx, patched):
    search = patched(Search([hit(113)]))

    learned = conversions.learn_for_lines(
        ["250 g flour", "1 cup water", "salt to taste"], None
    )

    assert learned == 0
    assert search.calls == 0


def test_the_number_of_lookups_per_import_is_bounded(ctx, patched):
    search = patched(Search([hit(113)]))
    # Distinct FOODS, not distinct spellings: the cache key strips digits,
    # so "thing1"/"thing2" would collapse to one lookup and the cap would
    # never be reached — which is how this test first passed vacuously.
    lines = [f"1 stick {chr(97 + i)}food" for i in range(20)]

    conversions.learn_for_lines(lines, None)

    assert search.calls == conversions.MAX_LOOKUPS_PER_IMPORT


def test_a_search_failure_does_not_fail_the_import(ctx, monkeypatch):
    class Broken:
        def enabled(self, settings=None):
            return True

        def web_search(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr(conversions, "websearch", Broken())

    assert conversions.learn_for_lines(["1 stick butter"], None) == 0


# --- the API ------------------------------------------------------------------

def make(client, **kw):
    """Create a conversion row inside the logged-in client's own group."""
    from app.models import User
    gid = db.session.query(User).first().group_id
    row = UnitConversion(unit="stick", food_term="butter", grams_per_unit=113.0,
                         source="web", source_url="https://example.com/a",
                         confidence=0.5, status="pending", group_id=gid, **kw)
    db.session.add(row)
    db.session.commit()
    return row


def test_the_list_shows_where_each_number_came_from(auth_client, app):
    with app.app_context():
        make(auth_client)

    [row] = auth_client.get("/api/v1/conversions").get_json()

    assert row["source"] == "web"
    assert row["sourceUrl"] == "https://example.com/a"
    assert row["status"] == "pending"


def test_accepting_a_pending_conversion_makes_it_usable(auth_client, app):
    with app.app_context():
        row = make(auth_client)
        rid = row.id

    auth_client.put(f"/api/v1/conversions/{rid}", json={"status": "confirmed"})

    with app.app_context():
        assert db.session.get(UnitConversion, rid).status == "confirmed"


def test_correcting_a_weight_stops_it_claiming_the_web_as_its_source(auth_client, app):
    with app.app_context():
        rid = make(auth_client).id

    body = auth_client.put(f"/api/v1/conversions/{rid}",
                           json={"gramsPerUnit": 115}).get_json()

    assert body["gramsPerUnit"] == 115
    assert body["source"] == "user", "a human's number must not be attributed to a page"
    assert body["sourceUrl"] == ""


@pytest.mark.parametrize("bad", [0, -5, 99999, "heavy"])
def test_an_implausible_correction_is_refused(auth_client, app, bad):
    with app.app_context():
        rid = make(auth_client).id

    assert auth_client.put(f"/api/v1/conversions/{rid}",
                           json={"gramsPerUnit": bad}).status_code == 422


def test_forgetting_a_conversion_removes_it(auth_client, app):
    with app.app_context():
        rid = make(auth_client).id

    assert auth_client.delete(f"/api/v1/conversions/{rid}").status_code == 204
    assert auth_client.get("/api/v1/conversions").get_json() == []


def test_another_households_conversion_is_not_reachable_by_id(auth_client, app):
    """Filtering by primary key alone is how one household edits another's data."""
    with app.app_context():
        from app.models.group import Group
        other = Group(name="Next door")
        db.session.add(other)
        db.session.flush()
        row = UnitConversion(unit="stick", food_term="butter", grams_per_unit=113.0,
                             source="web", confidence=1.0, status="confirmed",
                             group_id=other.id)
        db.session.add(row)
        db.session.commit()
        rid = row.id

    assert auth_client.get("/api/v1/conversions").get_json() == []
    assert auth_client.put(f"/api/v1/conversions/{rid}",
                           json={"status": "confirmed"}).status_code == 404
    assert auth_client.delete(f"/api/v1/conversions/{rid}").status_code == 404


def test_the_conversions_list_requires_a_login(client):
    assert client.get("/api/v1/conversions").status_code == 401
