"""Nutrition comes from USDA arithmetic when the reference data can answer it,
and from the model only when it cannot.

The load-bearing property is the SECOND one: a dataset is operator-built and
absent on essentially every install, so this must change nothing for anyone who
has not imported one.
"""
import pytest

from app.services.ai import nutrition as nut


class _Boom:
    """A provider that fails the test if it is consulted at all."""

    def complete_json(self, *a, **kw):  # pragma: no cover - must not run
        raise AssertionError("a model was asked to do arithmetic")


class _Fake:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete_json(self, *a, **kw):
        self.calls += 1
        return self.payload


def test_without_a_dataset_the_model_path_is_used_unchanged(monkeypatch):
    """MUTATION GUARD for the whole feature's safety story. No dataset is the
    shipped default; the estimate must arrive exactly as it does today."""
    monkeypatch.setattr(nut, "compute_nutrition", lambda *a, **kw: None)
    provider = _Fake({"calories": 520, "protein": 28, "carbs": 45, "fat": 22,
                      "fiber": 6, "sugar": 8, "sodium": 640})

    got = nut.nutrition_for_recipe(["2 cups flour", "3 eggs"], 4, provider)

    assert provider.calls == 1
    assert got["calories"] == 520
    assert got["source"] == nut.SOURCE_ESTIMATED


def test_a_computed_result_never_consults_a_model(monkeypatch):
    computed = {"calories": 311.0, "protein": 9.1, "source": nut.SOURCE_COMPUTED}
    monkeypatch.setattr(nut, "compute_nutrition", lambda *a, **kw: computed)

    got = nut.nutrition_for_recipe(["2 cups flour"], 4, _Boom())

    assert got["source"] == nut.SOURCE_COMPUTED


def test_no_provider_and_no_dataset_is_an_empty_result_not_a_crash(monkeypatch):
    monkeypatch.setattr(nut, "compute_nutrition", lambda *a, **kw: None)

    assert nut.nutrition_for_recipe(["2 cups flour"], 4, None) == {}


def test_a_stored_figure_says_where_it_came_from():
    """`0` used to mean both "zero" and "didn't know", and nothing recorded
    whether a number was multiplied or guessed. A reader — human or code —
    must be able to tell those apart."""
    est = nut.sanitize({"calories": 500}, source=nut.SOURCE_ESTIMATED)
    man = nut.sanitize({"calories": 500}, source=nut.SOURCE_MANUAL)

    assert est["source"] == nut.SOURCE_ESTIMATED
    assert man["source"] == nut.SOURCE_MANUAL
    assert nut.SOURCE_COMPUTED in nut.SOURCES


def test_an_unknown_source_label_is_refused():
    """MUTATION GUARD. The label is a claim about evidence, so an arbitrary
    string must not be storable — that is how a guess ends up presented as
    arithmetic. sanitize RAISES rather than quietly dropping it, which is the
    stronger choice: a caller inventing a label learns immediately."""
    with pytest.raises(ValueError):
        nut.sanitize({"calories": 500}, source="definitely-measured")


def test_the_endpoint_falls_back_to_the_model_when_there_is_no_dataset(
        auth_client, monkeypatch, app):
    """End-to-end through the route the UI actually calls."""
    from app.services.ai import registry

    monkeypatch.setattr(nut, "compute_nutrition", lambda *a, **kw: None)
    monkeypatch.setattr(registry, "get_provider",
                        lambda *a, **kw: _Fake({"calories": 400}))
    import app.api.ai as ai_api
    monkeypatch.setattr(ai_api, "get_provider",
                        lambda *a, **kw: _Fake({"calories": 400}))

    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Nutri", "servings": 4,
        "ingredients": [{"display": "2 cups flour"}],
    }).get_json()["id"]

    r = auth_client.post(f"/api/v1/ai/nutrition/{rid}")

    assert r.status_code == 200, r.get_json()
    assert r.get_json()["nutrition"]["calories"] == 400


def test_the_endpoint_refuses_only_when_both_paths_decline(
        auth_client, monkeypatch):
    """Previously a missing provider was fatal up front. With a dataset there
    is nothing to ask a model, so the refusal has to come last — and say so."""
    from app.services.ai.base import ProviderError
    import app.api.ai as ai_api

    monkeypatch.setattr(nut, "compute_nutrition", lambda *a, **kw: None)

    def _no_provider(*a, **kw):
        raise ProviderError("none configured")

    monkeypatch.setattr(ai_api, "get_provider", _no_provider)

    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Nutri2", "servings": 4,
        "ingredients": [{"display": "2 cups flour"}],
    }).get_json()["id"]

    r = auth_client.post(f"/api/v1/ai/nutrition/{rid}")

    assert r.status_code == 503
    assert "reference data" in r.get_json()["error"]


# --- the label has to reach the UI ------------------------------------------

def test_the_serializer_carries_the_source_and_coverage(auth_client, app):
    """MUTATION GUARD. _effective_nutrition keeps only NUMERIC values, so the
    label and coverage were being filtered out one layer below the UI — the
    panel could never have shown them however well the backend recorded them.
    """
    import json as _json

    from app.models import Recipe
    from app.extensions import db

    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Labelled", "servings": 2,
        "ingredients": [{"display": "2 cups flour"}],
    }).get_json()["id"]

    with app.app_context():
        r = db.session.get(Recipe, rid)
        r.nutrition = _json.dumps({"calories": 311.0, "source": "computed",
                                   "completeness": 0.87})
        db.session.commit()

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()

    assert got["nutritionSource"] == "computed"
    assert got["nutritionCompleteness"] == 87.0
    # The numeric sum must not have absorbed the label as a value.
    assert "source" not in (got["effectiveNutrition"] or {})


def test_an_unlabelled_legacy_figure_reports_unknown_not_computed(
        auth_client, app):
    """A figure stored before labelling existed must not be presented as
    arithmetic. Absence means unknown."""
    import json as _json

    from app.models import Recipe
    from app.extensions import db

    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Legacy", "servings": 2,
        "ingredients": [{"display": "2 cups flour"}],
    }).get_json()["id"]

    with app.app_context():
        r = db.session.get(Recipe, rid)
        r.nutrition = _json.dumps({"calories": 500.0})   # no source key
        db.session.commit()

    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()

    assert got["nutritionSource"] is None
    assert got["nutritionCompleteness"] is None


def test_one_unlabelled_component_makes_the_whole_total_unknown(
        auth_client, app):
    """MUTATION GUARD, and the case that exposed a dead branch.

    A sum is only as trustworthy as its vaguest part. A parent whose own figure
    is computed, linked to a sub-recipe whose figure predates labelling, must
    NOT present the total as arithmetic — the component's contribution is a
    mystery and it is inside the number.
    """
    import json as _json

    from app.models import Recipe
    from app.extensions import db

    sub = auth_client.post("/api/v1/recipes", json={
        "name": "Mystery sauce", "servings": 2,
        "ingredients": [{"display": "1 cup tomatoes"}],
    }).get_json()["id"]
    parent = auth_client.post("/api/v1/recipes", json={
        "name": "Parent", "servings": 2,
        "ingredients": [{"display": "1 batch Mystery sauce", "refRecipeId": sub}],
    }).get_json()["id"]

    with app.app_context():
        db.session.get(Recipe, parent).nutrition = _json.dumps(
            {"calories": 100.0, "source": "computed"})
        db.session.get(Recipe, sub).nutrition = _json.dumps(
            {"calories": 50.0})          # unlabelled, pre-provenance
        db.session.commit()

    got = auth_client.get(f"/api/v1/recipes/{parent}").get_json()

    assert got["effectiveNutrition"]["calories"] == 150.0   # still summed
    assert got["nutritionSource"] is None                   # but not claimed


def test_a_model_guessed_component_downgrades_a_computed_parent(
        auth_client, app):
    import json as _json

    from app.models import Recipe
    from app.extensions import db

    sub = auth_client.post("/api/v1/recipes", json={
        "name": "Guessed sauce", "servings": 2,
        "ingredients": [{"display": "1 cup tomatoes"}],
    }).get_json()["id"]
    parent = auth_client.post("/api/v1/recipes", json={
        "name": "Parent2", "servings": 2,
        "ingredients": [{"display": "1 batch Guessed sauce", "refRecipeId": sub}],
    }).get_json()["id"]

    with app.app_context():
        db.session.get(Recipe, parent).nutrition = _json.dumps(
            {"calories": 100.0, "source": "computed"})
        db.session.get(Recipe, sub).nutrition = _json.dumps(
            {"calories": 50.0, "source": "estimated"})
        db.session.commit()

    got = auth_client.get(f"/api/v1/recipes/{parent}").get_json()

    assert got["nutritionSource"] == "estimated"
