"""Recipe safety: the seam, the API shape, and the four states.

Written as a set of claims that each FAIL when the guard they describe is
removed. The three that matter most are marked MUTATION-CHECK and were each
verified by deleting the relevant code and watching this file go red:

  * STRICT still excludes `unknown` (delete `AllergenState.UNKNOWN` from
    foodcore's `_EXCLUDED_UNDER[STRICT]` → `test_strict_excludes_unknown...`
    fails).
  * Evidence survives to the API (drop `evidence` from `recipe_safety_out` →
    `test_every_evidence_field_reaches_the_wire` fails).
  * An empty `Food.allergens` is never read as free-from (make the endpoint
    consult `Food.allergens` → `test_an_empty_food_allergens_list_is_never_a_
    clearance` fails).
"""
import pytest

from app.services import food_reference as fr


# --- fixtures ---------------------------------------------------------------

#: Wheat, egg and milk, all unambiguous. One recipe per state the UI renders.
CLEAR_ALLERGEN_LINES = ["200 g wheat flour", "2 eggs", "100 ml milk", "50 g butter"]
#: Nothing the ruleset can read, so every allergen is `unknown`.
ONLY_UNKNOWN_LINES = ["1 jar of grandma's secret sauce", "2 blorps of flonk"]
#: Every line recognised, nothing matching any allergen rule.
NOTHING_IDENTIFIED_LINES = ["2 cups water", "1 tsp salt", "500 g potato"]


def _make_recipe(auth_client, name, lines):
    payload = {
        "name": name,
        "servings": 2,
        "ingredients": [{"display": line} for line in lines],
        "steps": [{"text": "Cook."}],
    }
    res = auth_client.post("/api/v1/recipes", json=payload)
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _safety(auth_client, recipe_id):
    res = auth_client.get(f"/api/v1/recipes/{recipe_id}/safety")
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _by_key(rows, key):
    for row in rows:
        if row["key"] == key:
            return row
    raise AssertionError(f"{key!r} not in {[r['key'] for r in rows]}")


# --- the seam ---------------------------------------------------------------

def test_the_seam_reports_all_four_states():
    """Each state is reachable, and `unknown` is distinct from "found nothing"."""
    contains = fr.screen_lines(CLEAR_ALLERGEN_LINES)
    assert contains.allergens.verdict("milk").state.value == "contains"

    unknown = fr.screen_lines(ONLY_UNKNOWN_LINES)
    assert unknown.allergens.verdict("milk").state.value == "unknown"

    nothing = fr.screen_lines(NOTHING_IDENTIFIED_LINES)
    assert (nothing.allergens.verdict("milk").state.value
            == "no_known_ingredient_identified")

    # may_contain: a precautionary declaration on the line's own text.
    may = fr.screen_lines(["100 g chocolate (may contain traces of peanuts)"])
    assert may.allergens.verdict("peanuts").state.value == "may_contain"


def test_the_fourth_state_says_out_loud_that_it_is_not_a_safety_claim():
    """The weakest-sounding state must not read as a clearance anywhere."""
    nothing = fr.screen_lines(NOTHING_IDENTIFIED_LINES)
    text = str(nothing.allergens.verdict("tree_nuts").state)
    assert "not a safety claim" in text


def test_recipe_safety_has_no_truth_value():
    """`if safety:` must blow up rather than mean "nothing found"."""
    safety = fr.screen_lines(NOTHING_IDENTIFIED_LINES)
    with pytest.raises(TypeError, match="no truth value"):
        bool(safety)
    with pytest.raises(TypeError, match="no truth value"):
        if safety:  # noqa: SIM103 - the point is that this raises
            pass


def test_the_seam_exposes_no_lenient_predicate():
    """No `is_safe`/`free_of`/`allergen_free` shortcut may be added to the seam.

    foodcore's whole API shape exists to force a policy to be named; a helper
    here would undo it from the host side in one line.
    """
    banned = [n for n in dir(fr)
              if any(w in n.lower() for w in ("is_safe", "free_of", "safe_for",
                                              "allergen_free", "is_ok"))]
    assert banned == []


def test_every_verdict_carries_evidence():
    """Including the fourth state, whose evidence is the coverage record."""
    for lines in (CLEAR_ALLERGEN_LINES, ONLY_UNKNOWN_LINES,
                  NOTHING_IDENTIFIED_LINES):
        safety = fr.screen_lines(lines)
        for verdict in safety.allergens.verdicts:
            assert verdict.evidence, verdict.allergen
        for verdict in safety.diets.verdicts:
            assert verdict.evidence, verdict.diet


def test_a_draft_with_no_ingredients_is_not_a_clearance():
    with pytest.raises(fr.NothingToScreen):
        fr.screen_lines([])
    with pytest.raises(fr.NothingToScreen):
        fr.screen_lines(["   ", ""])


def test_an_absent_engine_raises_rather_than_returning_an_empty_result(monkeypatch):
    """Degrading to None here would render as "nothing found"."""
    def boom():
        raise fr.ScreeningUnavailable("nope")

    monkeypatch.setattr(fr, "_safety_engines", boom)
    with pytest.raises(fr.ScreeningUnavailable):
        fr.screen_lines(CLEAR_ALLERGEN_LINES)


def test_an_engine_failure_is_not_echoed_at_the_caller(monkeypatch):
    """A typed domain exception with a curated message, not the engine's text."""
    import foodcore.allergens as fca
    import foodcore.dietary as fcd

    class Exploding:
        def __getattr__(self, name):
            raise RuntimeError("postgresql://user:hunter2@db/secret")

    monkeypatch.setattr(fr, "_safety_engines", lambda: (Exploding(), fcd))
    with pytest.raises(fr.ScreeningUnavailable) as caught:
        fr.screen_lines(CLEAR_ALLERGEN_LINES)
    assert "hunter2" not in str(caught.value)
    assert "could not be completed" in str(caught.value)
    assert caught.value.__cause__ is None  # `from None`, so no chained leak
    assert fca is not None  # import kept meaningful


def test_an_optional_line_is_screened_as_optional():
    """A contains downgrades to may_contain when the line is optional."""
    plain = fr.screen_lines([fr.SafetyLine(text="50 g peanuts")])
    assert plain.allergens.verdict("peanuts").state.value == "contains"
    opt = fr.screen_lines([fr.SafetyLine(text="50 g peanuts", optional=True)])
    assert opt.allergens.verdict("peanuts").state.value == "may_contain"


def test_a_blank_line_cannot_be_constructed():
    """The one way this seam could manufacture a false clearance.

    foodcore reads "" as a line it DID read and matched nothing in, so a blank
    `SafetyLine` would contribute a clearance to every allergen while looking
    like an ordinary screened row.
    """
    with pytest.raises(ValueError, match="blank"):
        fr.SafetyLine(text="")
    with pytest.raises(ValueError, match="blank"):
        fr.SafetyLine(text="   ")


def test_a_components_line_is_explained_by_its_components():
    """Both directions of foodcore's `components` contract.

    WITH components, the parent's own text stops being charged an opacity
    `unknown` and the children are screened. WITHOUT them, the parent's words
    are all there is and an opaque one is `unknown` — never nothing.
    """
    expanded = fr.screen_lines([
        fr.SafetyLine(text="1 batch secret sauce",
                      components=(fr.SafetyLine(text="200 g peanut butter"),)),
        fr.SafetyLine(text="200 g rice noodles"),
    ])
    assert expanded.allergens.verdict("peanuts").state.value == "contains"
    assert expanded.allergens.fully_screened is True
    # Three coverage rows: the parent and its child both count as read.
    assert len(expanded.allergens.coverage) == 3

    unexpanded = fr.screen_lines([
        fr.SafetyLine(text="1 batch secret sauce"),
        fr.SafetyLine(text="200 g rice noodles"),
    ])
    assert unexpanded.allergens.verdict("milk").state.value == "unknown"
    assert unexpanded.allergens.fully_screened is False


# --- MUTATION-CHECK: STRICT excludes unknown --------------------------------

def test_strict_excludes_unknown_exactly_like_contains():
    """The rule that protects someone with a real allergy.

    MUTATION-CHECK: removing `AllergenState.UNKNOWN` from foodcore's
    `_EXCLUDED_UNDER[AvoidancePolicy.STRICT]` makes this fail.
    """
    avoidance, _diet = fr.screening_policies()
    safety = fr.screen_lines(ONLY_UNKNOWN_LINES)
    verdict = safety.allergens.verdict("peanuts")
    assert verdict.state.value == "unknown"

    # Excluded, not flagged, not passed-with-a-warning.
    assert verdict.permitted_under(avoidance.STRICT) is False
    strict = safety.allergens.decide(avoidance.STRICT)
    assert strict.allowed is False
    assert verdict in strict.blocking
    assert verdict not in strict.flagged

    # And a `contains` verdict is excluded the same way — "exactly like".
    contains = fr.screen_lines(CLEAR_ALLERGEN_LINES).allergens.verdict("milk")
    assert contains.permitted_under(avoidance.STRICT) is False


def test_preference_passes_unknown_but_surfaces_it_as_flagged():
    """The other half of the pair: PREFERENCE must not silently swallow it."""
    avoidance, _diet = fr.screening_policies()
    safety = fr.screen_lines(ONLY_UNKNOWN_LINES)
    verdict = safety.allergens.verdict("peanuts")
    assert verdict.permitted_under(avoidance.STRICT) is False
    assert verdict.permitted_under(avoidance.PREFERENCE) is True
    decision = safety.allergens.decide(avoidance.PREFERENCE)
    assert decision.allowed is True
    assert verdict in decision.flagged


def test_strict_excludes_dietary_uncertainty_the_same_way():
    _avoidance, diet = fr.screening_policies()
    safety = fr.screen_lines(ONLY_UNKNOWN_LINES)
    vegan = safety.diets.verdict("vegan")
    assert vegan.state.value in ("uncertain", "unknown")
    assert vegan.permitted_under(diet.STRICT) is False
    assert vegan.permitted_under(diet.PREFERENCE) is True


def test_a_policy_cannot_be_omitted_or_faked():
    avoidance, _diet = fr.screening_policies()
    safety = fr.screen_lines(CLEAR_ALLERGEN_LINES)
    with pytest.raises(TypeError):
        safety.allergens.decide()
    with pytest.raises(TypeError, match="no default on purpose"):
        safety.allergens.decide("strict")
    assert safety.allergens.decide(avoidance.STRICT) is not None


# --- MUTATION-CHECK: evidence survives to the API ---------------------------

def _camel(name):
    head, *rest = name.split("_")
    return head + "".join(w.title() for w in rest)


def _expected_fields(dataclass_type):
    """The camelCase names foodcore's own dataclass declares.

    Derived, not hardcoded. A hardcoded set passes forever: foodcore could add
    a field to `Evidence` or `Provenance`, the serializer would silently drop
    it, and the assertion would stay green — which is the exact failure mode
    (`_effective_nutrition`) this payload was written longhand to avoid. The
    ruleset is versioned precisely because it is expected to change, so the
    test that guards its shape has to change with it.
    """
    import dataclasses

    return {_camel(f.name) for f in dataclasses.fields(dataclass_type)}


def _evidence_fields():
    import foodcore.allergens as fca
    import foodcore.dietary as fcd

    allergen = _expected_fields(fca.Evidence)
    diet = _expected_fields(fcd.DietEvidence)
    assert allergen == diet, (
        "the two evidence types have diverged; _evidence_out serializes both "
        f"with one function. allergen-only={allergen - diet} "
        f"diet-only={diet - allergen}")
    return allergen


def _provenance_fields():
    import foodcore

    return _expected_fields(foodcore.Provenance)


def test_the_expected_field_sets_come_from_foodcore_not_from_memory():
    """Guards the guard: if this drifts, the assertions below mean nothing."""
    assert _evidence_fields() == {
        "ingredient", "matchedTerm", "ruleId", "ruleVersion", "matchKind",
        "contributes", "note", "provenance"}
    assert _provenance_fields() == {
        "source", "sourceId", "retrieved", "method", "version", "confidence",
        "license"}


def test_every_evidence_field_reaches_the_wire(auth_client):
    """MUTATION-CHECK: dropping `evidence` (or any one of its fields) from
    `recipe_safety_out` makes this fail. This is the mistake `_effective_
    nutrition` made one layer below the UI — non-numeric fields silently gone."""
    recipe = _make_recipe(auth_client, "Pancakes", CLEAR_ALLERGEN_LINES)
    data = _safety(auth_client, recipe["id"])

    milk = _by_key(data["allergens"], "milk")
    assert milk["state"] == "contains"
    assert milk["evidence"], "a verdict with no evidence must never serialize"
    for row in milk["evidence"]:
        assert set(row) == _evidence_fields()
        assert row["ruleId"] and row["ruleVersion"] and row["matchKind"]
        assert row["contributes"] in {"contains", "may_contain", "unknown",
                                      "no_known_ingredient_identified"}
        assert set(row["provenance"]) == _provenance_fields()
        assert row["provenance"]["version"]
        assert row["provenance"]["confidence"] is not None

    # The specific trail, not just its shape: butter is milk by derivative rule.
    terms = {(e["ingredient"], e["matchedTerm"]) for e in milk["evidence"]}
    assert ("50 g butter", "butter") in terms

    # Dietary evidence is held to the same standard.
    vegan = _by_key(data["diets"], "vegan")
    assert vegan["state"] == "incompatible"
    for row in vegan["evidence"]:
        assert set(row) == _evidence_fields()


def test_the_fourth_state_keeps_its_coverage_evidence_on_the_wire(auth_client):
    """"We looked and found nothing" is only meaningful with the looking shown."""
    recipe = _make_recipe(auth_client, "Boiled Potatoes", NOTHING_IDENTIFIED_LINES)
    data = _safety(auth_client, recipe["id"])
    nuts = _by_key(data["allergens"], "tree_nuts")
    assert nuts["state"] == "no_known_ingredient_identified"
    assert "not a safety claim" in nuts["stateText"]
    assert len(nuts["evidence"]) == 1
    note = nuts["evidence"][0]["note"]
    assert "3 ingredient line(s) screened" in note
    assert "not a safety claim" in note


def test_unknown_evidence_names_the_line_and_the_reason(auth_client):
    recipe = _make_recipe(auth_client, "Mystery Stew", ONLY_UNKNOWN_LINES)
    data = _safety(auth_client, recipe["id"])
    assert data["fullyScreened"] is False
    sesame = _by_key(data["allergens"], "sesame")
    assert sesame["state"] == "unknown"
    notes = " ".join(e["note"] for e in sesame["evidence"])
    assert "composite whose ingredients are not declared" in notes
    assert "unrecognised" in notes

    # Per-line coverage carries the same reasons, keyed by ingredient.
    reasons = {c["ingredient"]: c["unscreened"] for c in data["coverage"]}
    assert any(reasons.values())
    assert "2 blorps of flonk" in reasons


# --- MUTATION-CHECK: an empty Food.allergens is never a clearance ----------

def test_an_empty_food_allergens_list_is_never_a_clearance(auth_client, app):
    """MUTATION-CHECK: make the endpoint read `Food.allergens` and this fails.

    `Food.allergens` is `[]` for everything a user typed. If the endpoint
    consulted it, a user-typed "peanut butter" would come back as having no
    allergens — the exact false clearance docs/food-intelligence-platform.md
    §3.2 exists to prevent. Screening reads the ingredient TEXT instead.
    """
    from app.extensions import db
    from app.models import Food

    recipe = _make_recipe(auth_client, "Satay", ["200 g peanut butter",
                                                 "100 ml milk"])
    with app.app_context():
        foods = db.session.query(Food).all()
        # Whatever the catalog happens to hold, force the "user typed it" state.
        for food in foods:
            food.allergens = []
        db.session.commit()
        assert all(f.allergens == [] for f in db.session.query(Food).all())

    data = _safety(auth_client, recipe["id"])
    assert _by_key(data["allergens"], "peanuts")["state"] == "contains"
    assert _by_key(data["allergens"], "milk")["state"] == "contains"
    # Nothing in the payload reports an allergen as absent-and-therefore-fine.
    assert "free" not in str(data).lower().replace("gluten_free", "") \
        .replace("gluten-free", "").replace("dairy_free", "") \
        .replace("dairy-free", "").replace("free-from", "")


def test_the_two_lexicons_disagree_about_coconut_and_that_is_correct(auth_client,
                                                                     app):
    """The documented trap, asserted so nobody "fixes" it into one list.

    `food_resolve` seeds coconut with NO allergens (botanically a drupe, and the
    material-boundary rule needs that). foodcore calls it a tree nut (FDA
    labelling). Both must stay true at the same time.
    """
    from app.services import food_resolve

    # myMeal's lexicon: every coconut form is seeded with NO allergens, and it
    # must stay that way — asserted directly against the seed table rather than
    # behind a hasattr(), so a rename fails loudly instead of skipping.
    for name in ("coconut", "coconut milk", "coconut oil", "desiccated coconut"):
        classification, allergens = food_resolve.SEED_FOODS[name]
        assert allergens == (), (
            f"{name!r} gained an allergen in SEED_FOODS; that table is the "
            "material/botanical lexicon and merging the FDA labelling view "
            "into it breaks the material-boundary rule (ADR-0001)")
        assert classification

    recipe = _make_recipe(auth_client, "Curry", ["400 ml coconut milk",
                                                 "200 g chicken"])
    with app.app_context():
        from app.extensions import db
        from app.models import Food
        for food in db.session.query(Food).all():
            assert "tree_nuts" not in (food.allergens or []), \
                "Food.allergens must stay the botanical/material lexicon"
        db.session.remove()

    # foodcore's lexicon: the same recipe declares tree nuts.
    data = _safety(auth_client, recipe["id"])
    nuts = _by_key(data["allergens"], "tree_nuts")
    assert nuts["state"] == "contains"
    assert any(e["matchedTerm"] == "coconut milk" for e in nuts["evidence"])
    # And the material boundary still holds: coconut milk is not dairy.
    assert _by_key(data["allergens"], "milk")["state"] in (
        "no_known_ingredient_identified", "unknown")


# --- the endpoint ------------------------------------------------------------

# --- a dropped line is a false clearance -----------------------------------
#
# Found by adversarial review and reproduced against the running app before
# being fixed. Every one of these returned `no_known_ingredient_identified`
# with `fullyScreened: true` — the engine reporting a line it never saw as a
# line it read and found clean. Each is written to fail if the row is ever
# dropped again, in any of the four ways it could be.

def test_a_component_whose_subrecipe_is_empty_is_unknown_not_clean(auth_client):
    """The parent line must survive an expansion that yields nothing.

    `iter_leaf_ingredients` recurses into the sub-recipe, finds no rows, and
    yields nothing at all — so flattening lost "1 batch Secret Peanut Sauce"
    entirely and peanuts came back clean.
    """
    stub = auth_client.post("/api/v1/recipes", json={
        "name": "Secret Peanut Sauce", "servings": 1, "ingredients": [],
        "steps": [{"text": "x"}]}).get_json()
    parent = auth_client.post("/api/v1/recipes", json={
        "name": "Noodles", "servings": 2, "steps": [{"text": "x"}],
        "ingredients": [
            {"display": "1 batch Secret Peanut Sauce", "quantity": 1,
             "refRecipeId": stub["id"]},
            {"display": "200 g rice noodles"},
        ]}).get_json()

    data = _safety(auth_client, parent["id"])

    assert data["screenedLines"] == 2
    assert data["fullyScreened"] is False
    assert any("Secret Peanut Sauce" in c["ingredient"] for c in data["coverage"])
    assert _by_key(data["allergens"], "milk")["state"] == "unknown"


def test_a_recipe_that_is_only_an_empty_component_is_not_no_ingredients(
        auth_client):
    """It has an ingredient row, so the panel must not say it has none."""
    stub = auth_client.post("/api/v1/recipes", json={
        "name": "Empty Sub", "servings": 1, "ingredients": [],
        "steps": [{"text": "x"}]}).get_json()
    parent = auth_client.post("/api/v1/recipes", json={
        "name": "Only A Component", "servings": 2, "steps": [{"text": "x"}],
        "ingredients": [{"display": "1 batch Empty Sub", "quantity": 1,
                         "refRecipeId": stub["id"]}]}).get_json()

    data = _safety(auth_client, parent["id"])

    assert data["status"] == "screened"
    assert data["screenedLines"] == 1
    assert data["fullyScreened"] is False


def test_a_row_with_a_food_but_no_display_is_screened_on_the_food(auth_client):
    """`display` is not required by the API; the SPA always sends one, a
    REST/MCP/HA client need not. Such a row used to be skipped entirely."""
    recipe = auth_client.post("/api/v1/recipes", json={
        "name": "Satay Sauce", "servings": 2, "steps": [{"text": "x"}],
        "ingredients": [
            {"food": "peanut butter", "quantity": 200, "unit": "g"},
            {"display": "1 tsp salt"},
        ]}).get_json()

    data = _safety(auth_client, recipe["id"])

    assert data["screenedLines"] == 2
    assert _by_key(data["allergens"], "peanuts")["state"] == "contains"


def test_a_row_with_no_text_at_all_is_unknown_not_clean(auth_client):
    """A quantity is not a food. Composing "2.0" made foodcore discard it as a
    bare numeral and report the line as read-and-clean."""
    recipe = auth_client.post("/api/v1/recipes", json={
        "name": "Mystery Row", "servings": 2, "steps": [{"text": "x"}],
        "ingredients": [{"quantity": 2}, {"display": "1 tsp salt"}]}).get_json()

    data = _safety(auth_client, recipe["id"])

    assert data["screenedLines"] == 2
    assert data["fullyScreened"] is False
    assert _by_key(data["allergens"], "peanuts")["state"] == "unknown"


def test_the_optional_flag_does_not_depend_on_how_the_line_is_phrased(
        auth_client):
    """`units.annotations` suppresses the flag when the word "optional" is
    still visible in the rendered text — right for display, wrong here. Two
    phrasings of one fact must not produce two different states."""
    # The first measures; the rest are the phrasings `annotations` suppresses
    # (it hides the flag when the word is still in the rendered text, and when
    # the line has no leading quantity). All five state the same fact.
    lines = [
        "50 g peanuts, optional",
        "50 g peanuts (optional)",
        "(optional) 50 g peanuts",
        "peanuts, optional",
        "a handful of peanuts, optional",
    ]
    states = {}
    for i, line in enumerate(lines):
        recipe = _make_recipe(auth_client, f"Optional {i}", [line])
        data = _safety(auth_client, recipe["id"])
        states[line] = _by_key(data["allergens"], "peanuts")["state"]
    assert set(states.values()) == {"may_contain"}, states


def test_safety_requires_auth(app, auth_client):
    """A FRESH client, not the `client` fixture: `auth_client` IS `client` with
    an Authorization header stapled on, so asking `client` would ask the
    logged-in one and pass with no gate at all."""
    recipe = _make_recipe(auth_client, "Toast", ["2 slices bread"])
    anon = app.test_client()
    assert anon.get(f"/api/v1/recipes/{recipe['id']}/safety").status_code == 401


def test_safety_is_group_scoped(auth_client, client):
    """A second household must not be able to screen someone else's recipe."""
    recipe = _make_recipe(auth_client, "Private Loaf", ["200 g wheat flour"])
    client.post("/api/v1/users/register",
                json={"email": "other@x.com", "password": "password2", "name": "O"})
    token = client.post("/api/v1/users/login",
                        json={"username": "other@x.com",
                              "password": "password2"}).get_json()["token"]
    client.environ_base["HTTP_AUTHORIZATION"] = token
    assert client.get(f"/api/v1/recipes/{recipe['id']}/safety").status_code == 404


def test_safety_resolves_by_slug_too(auth_client):
    recipe = _make_recipe(auth_client, "Slug Cake", CLEAR_ALLERGEN_LINES)
    res = auth_client.get(f"/api/v1/recipes/{recipe['slug']}/safety")
    assert res.status_code == 200
    assert res.get_json()["screenedLines"] == 4


def test_the_payload_states_what_it_is_bounded_by(auth_client):
    recipe = _make_recipe(auth_client, "Pancakes 2", CLEAR_ALLERGEN_LINES)
    data = _safety(auth_client, recipe["id"])
    assert data["status"] == "screened"
    assert data["screenedLines"] == 4
    assert data["fullyScreened"] is True
    assert data["rulesetVersions"]["allergens"]
    assert data["rulesetVersions"]["dietary"]
    assert len(data["coverage"]) == 4
    # Nine US major allergens, nine diets — foodcore's own default sets.
    assert len(data["allergens"]) == 9
    assert len(data["diets"]) == 9


def test_an_empty_recipe_says_so_rather_than_reporting_no_allergens(auth_client):
    """Absent arrays, not empty ones: `[]` is the shape a UI renders as "none"."""
    recipe = _make_recipe(auth_client, "Stub", [])
    res = auth_client.get(f"/api/v1/recipes/{recipe['id']}/safety")
    assert res.status_code == 200
    data = res.get_json()
    assert data == {"status": "no_ingredients", "screenedLines": 0}
    assert "allergens" not in data
    assert "diets" not in data


def test_an_unavailable_engine_is_a_503_not_an_empty_panel(auth_client,
                                                           monkeypatch):
    recipe = _make_recipe(auth_client, "Pancakes 3", CLEAR_ALLERGEN_LINES)

    def unavailable(_lines):
        raise fr.ScreeningUnavailable("Screening is unavailable on this install.")

    monkeypatch.setattr(fr, "screen_lines", unavailable)
    res = auth_client.get(f"/api/v1/recipes/{recipe['id']}/safety")
    assert res.status_code == 503
    body = res.get_json()
    assert body["status"] == "unavailable"
    assert body["error"] == "Screening is unavailable on this install."
    assert "allergens" not in body


def test_a_missing_recipe_is_a_404(auth_client):
    assert auth_client.get(
        "/api/v1/recipes/does-not-exist/safety").status_code == 404


def test_screening_does_not_depend_on_the_foodcore_dataset(auth_client,
                                                           monkeypatch):
    """The rules ship in the package; only grams and nutrition need the dataset."""
    monkeypatch.setattr(fr, "dataset_path", lambda settings=None: "")
    monkeypatch.setattr(fr, "_dataset", lambda settings=None: None)
    recipe = _make_recipe(auth_client, "No Dataset", CLEAR_ALLERGEN_LINES)
    data = _safety(auth_client, recipe["id"])
    assert _by_key(data["allergens"], "wheat")["state"] == "contains"


def test_a_component_subrecipe_is_screened_by_its_real_contents(auth_client,
                                                                app):
    """Screening goes through the one shared expander, so a linked sub-recipe
    contributes its own ingredients rather than the words "1 batch stock"."""
    stock = _make_recipe(auth_client, "Peanut Stock", ["100 g peanuts",
                                                       "1 l water"])
    parent = auth_client.post("/api/v1/recipes", json={
        "name": "Soup",
        "servings": 4,
        "ingredients": [
            {"display": "1 batch Peanut Stock", "quantity": 1,
             "refRecipeId": stock["id"]},
            {"display": "2 carrots"},
        ],
        "steps": [{"text": "Simmer."}],
    })
    assert parent.status_code == 201, parent.get_json()
    data = _safety(auth_client, parent.get_json()["id"])
    peanuts = _by_key(data["allergens"], "peanuts")
    assert peanuts["state"] == "contains"
    # The evidence names the sub-recipe's line, not the parent's component row.
    assert any("peanut" in e["ingredient"].lower() for e in peanuts["evidence"])

    # `screenedLines` counts what the ENGINE read, not the rows handed to it:
    # the parent's two rows plus the sub-recipe's two. Counting the top level
    # would under-report a compound recipe by exactly the part a cook most
    # wants counted, and the number is what every state below it is bounded by.
    assert data["screenedLines"] == 4
    assert len(data["coverage"]) == 4
    assert sum("Peanut Stock →" in c["ingredient"] for c in data["coverage"]) == 2
