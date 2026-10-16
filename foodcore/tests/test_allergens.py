"""Allergen engine tests, including the curated evaluation set from §14.

Two acceptance targets from the spec are asserted directly, and both are
mutation-checked (break the guard, watch the named test go red, restore):

* ``test_acceptance_no_known_excluded_allergen_passes_a_hard_constraint``
* ``test_acceptance_unknown_is_never_automatically_safe``
"""
from __future__ import annotations

import pytest

from app.allergens import (
    US_MAJOR_ALLERGENS,
    AllergenState,
    AllergenVerdict,
    AvoidancePolicy,
    IngredientRef,
    SensitivityProfile,
    resolve_allergen_key,
    screen,
    screen_text,
)
from app.rules import allergen_ruleset

ALL_KEYS = tuple(allergen_ruleset().labels)
EVERYTHING = SensitivityProfile(allergens=ALL_KEYS)


def state_for(text, allergen, **kwargs):
    profile = SensitivityProfile(allergens=(allergen,), **kwargs)
    return screen_text(text, profile).verdict(allergen).state


def verdict_for(text, allergen):
    return screen_text(text, SensitivityProfile(allergens=(allergen,))).verdict(allergen)


# --- the four states and the shape of the API --------------------------------

def test_the_fourth_state_is_not_spelled_safe():
    # The name is the documentation: nothing in this module says "safe",
    # "free" or "ok" about an absence of evidence.
    assert AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED.value == (
        "no_known_ingredient_identified"
    )
    assert "not a safety claim" in str(AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED)
    for state in AllergenState:
        assert not any(w in state.value for w in ("safe", "free", "ok", "pass"))


def test_a_verdict_has_no_truth_value():
    v = verdict_for("almond milk", "milk")
    with pytest.raises(TypeError, match="no truth value"):
        bool(v)
    with pytest.raises(TypeError, match="no truth value"):
        if v:  # the shortest unsafe thing a caller could write
            pass


def test_a_screening_has_no_truth_value():
    screening = screen_text("almond milk", EVERYTHING)
    with pytest.raises(TypeError, match="no truth value"):
        bool(screening)


def test_there_is_no_boolean_without_an_explicit_policy():
    v = verdict_for("whole milk", "milk")
    with pytest.raises(TypeError):
        v.permitted_under("strict")  # a string is not a policy
    with pytest.raises(TypeError):
        v.permitted_under(None)
    with pytest.raises(TypeError):
        v.permitted_under()  # type: ignore[call-arg]
    assert v.permitted_under(AvoidancePolicy.STRICT) is False


def test_the_api_exposes_no_safety_shortcut():
    v = verdict_for("1 tsp salt", "milk")
    for forbidden in ("is_safe", "safe", "ok", "passes", "allowed", "free_of"):
        assert not hasattr(v, forbidden), forbidden


def test_every_verdict_carries_evidence_including_the_fourth_state():
    screening = screen(["1 tsp salt", "1 onion"], EVERYTHING)
    for v in screening.verdicts:
        assert v.evidence, v.allergen
        for ev in v.evidence:
            assert ev.rule_version == screening.ruleset_version
            assert ev.rule_id
            assert ev.provenance is not None
            assert ev.provenance.version == screening.ruleset_version


def test_a_verdict_without_evidence_is_a_bug():
    with pytest.raises(ValueError, match="no evidence"):
        AllergenVerdict(
            allergen="milk", label="Milk", state=AllergenState.CONTAINS,
            evidence=(), ruleset_version="x",
        )


def test_decision_allowed_cannot_disagree_with_blocking():
    screening = screen(["200ml whole milk"], SensitivityProfile(allergens=("milk",)))
    decision = screening.decide(AvoidancePolicy.STRICT)
    assert decision.allowed is False
    assert [v.allergen for v in decision.blocking] == ["milk"]
    assert decision.reasons()


def test_the_default_profile_covers_the_nine_us_major_allergens():
    screening = screen(["1 onion"])
    assert [v.allergen for v in screening.verdicts] == list(US_MAJOR_ALLERGENS)
    assert len(US_MAJOR_ALLERGENS) == 9


# --- the material boundary (ADR-0001) ----------------------------------------

@pytest.mark.parametrize("text,head_allergen,real_allergen", [
    ("almond milk", "milk", "tree_nuts"),
    ("200ml unsweetened almond milk", "milk", "tree_nuts"),
    ("cashew milk", "milk", "tree_nuts"),
    ("soy milk", "milk", "soybeans"),
    ("coconut milk", "milk", "tree_nuts"),
    ("peanut butter", "milk", "peanuts"),
    ("almond butter", "milk", "tree_nuts"),
    ("rice flour", "wheat", None),
    ("cornflour", "wheat", None),
    ("buckwheat flour", "wheat", None),
    ("cocoa butter", "milk", None),
    ("water chestnuts", "tree_nuts", None),
    ("1 aubergine", "egg", None),
    ("butternut squash", "tree_nuts", None),
])
def test_a_match_never_crosses_a_material_boundary(text, head_allergen, real_allergen):
    """"almond milk" must never read as milk — the ADR-0001 rule, which is what
    makes this engine safe to put in front of someone with a dairy allergy."""
    assert state_for(text, head_allergen) is (
        AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED
    )
    if real_allergen:
        assert state_for(text, real_allergen) is AllergenState.CONTAINS


def test_the_boundary_match_says_why():
    v = verdict_for("almond milk", "tree_nuts")
    kinds = {ev.match_kind for ev in v.evidence}
    assert "material_boundary" in kinds
    assert any("ADR-0001" in ev.note for ev in v.evidence)


def test_an_unlisted_compound_over_restricts_rather_than_clearing():
    # "tiger nut milk" IS listed; "yak milk" is not. The unlisted one must
    # still exclude, never clear: a missing rule costs a false exclusion.
    assert state_for("tiger nut milk", "milk") is (
        AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED
    )
    assert state_for("yak milk", "milk") is AllergenState.CONTAINS


# --- derived substances and aliases ------------------------------------------

@pytest.mark.parametrize("text,allergen", [
    ("casein", "milk"),
    ("sodium caseinate", "milk"),
    ("whey protein", "milk"),
    ("lactose", "milk"),
    ("ghee", "milk"),
    ("50g parmesan", "milk"),
    ("semolina", "wheat"),
    ("durum wheat", "wheat"),
    ("100g panko breadcrumbs", "wheat"),
    ("seitan", "wheat"),
    ("2 tbsp tahini", "sesame"),
    ("halva", "sesame"),
    ("200g hummus", "sesame"),
    ("marzipan", "tree_nuts"),
    ("frangipane", "tree_nuts"),
    ("arachis oil", "peanuts"),
    ("satay sauce", "peanuts"),
    ("worcestershire sauce", "fish"),
    ("2 tbsp fish sauce", "fish"),
    ("albumen", "egg"),
    ("mayonnaise", "egg"),
    ("miso paste", "soybeans"),
    ("textured vegetable protein", "soybeans"),
    ("2 tbsp soy sauce", "soybeans"),
    ("2 tbsp soy sauce", "wheat"),
    ("200g king prawns", "crustacean_shellfish"),
    ("shrimp paste", "crustacean_shellfish"),
    ("sodium metabisulfite", "sulfites"),
    ("malt vinegar", "gluten"),
])
def test_derived_substances_and_aliases_are_identified(text, allergen):
    assert state_for(text, allergen) is AllergenState.CONTAINS


def test_an_alias_match_records_which_rule_fired():
    v = verdict_for("casein", "milk")
    ev = next(e for e in v.evidence if e.contributes is AllergenState.CONTAINS)
    assert ev.matched_term == "casein"
    assert ev.match_kind == "derivative"
    assert ev.rule_id == "milk.derivative.casein"
    assert ev.provenance.method == "inferred"


def test_wheat_implies_gluten():
    assert state_for("semolina", "gluten") is AllergenState.CONTAINS
    v = verdict_for("semolina", "gluten")
    assert any(ev.rule_id.startswith("wheat.") for ev in v.evidence)


# --- ambiguity, precaution, optionality, labels ------------------------------

def test_an_ambiguous_term_can_never_reach_contains():
    assert state_for("100g dark chocolate", "milk") is AllergenState.MAY_CONTAIN
    assert state_for("200g noodles", "wheat") is AllergenState.MAY_CONTAIN
    assert state_for("1 tsp lecithin", "soybeans") is AllergenState.MAY_CONTAIN
    v = verdict_for("200g noodles", "wheat")
    assert any("may be wheat" in ev.note for ev in v.evidence)


def test_a_precautionary_statement_is_may_contain_not_contains():
    assert state_for(
        "oat biscuits (may contain traces of nuts)", "tree_nuts"
    ) is AllergenState.MAY_CONTAIN
    v = verdict_for("dark chocolate, produced in a facility that handles peanuts",
                    "peanuts")
    assert v.state is AllergenState.MAY_CONTAIN
    assert any(ev.match_kind == "precautionary_statement" for ev in v.evidence)


def test_an_optional_ingredient_is_may_contain_and_still_blocked():
    screening = screen(
        [IngredientRef("1 tsp salt"), IngredientRef("flaked almonds", optional=True)],
        SensitivityProfile(allergens=("tree_nuts",)),
    )
    v = screening.verdict("tree_nuts")
    assert v.state is AllergenState.MAY_CONTAIN
    assert any(ev.match_kind == "optional_ingredient" for ev in v.evidence)
    assert v.permitted_under(AvoidancePolicy.STRICT) is False
    assert v.permitted_under(AvoidancePolicy.PREFERENCE) is True


def test_a_declared_label_allergen_outranks_the_text():
    ing = IngredientRef("Brand X brioche bun", declared_contains=("milk", "egg"))
    screening = screen([ing], SensitivityProfile(allergens=("milk", "egg")))
    for key in ("milk", "egg"):
        v = screening.verdict(key)
        assert v.state is AllergenState.CONTAINS
        ev = next(e for e in v.evidence if e.match_kind == "declared_label")
        assert ev.provenance.method == "declared"
        assert ev.provenance.confidence == 1.0


def test_a_declared_may_contain_label_is_may_contain():
    ing = IngredientRef("Brand X cereal", declared_may_contain=("sesame",))
    v = screen([ing], SensitivityProfile(allergens=("sesame",))).verdict("sesame")
    assert v.state is AllergenState.MAY_CONTAIN


def test_a_free_from_claim_downgrades_but_never_clears():
    v = verdict_for("gluten free bread", "wheat")
    assert v.state is AllergenState.MAY_CONTAIN
    assert any(ev.match_kind == "declared_free_from" for ev in v.evidence)
    assert any("not verified" in ev.note for ev in v.evidence)
    assert v.permitted_under(AvoidancePolicy.STRICT) is False
    assert v.permitted_under(AvoidancePolicy.PREFERENCE) is True


def test_a_declared_label_beats_a_free_from_claim():
    ing = IngredientRef("dairy free spread", declared_contains=("milk",))
    v = screen([ing], SensitivityProfile(allergens=("milk",))).verdict("milk")
    assert v.state is AllergenState.CONTAINS


def test_a_compound_ingredient_is_screened_through_its_components():
    opaque = IngredientRef("house seasoning blend")
    assert screen([opaque], SensitivityProfile(allergens=("sesame",))).verdict(
        "sesame"
    ).state is AllergenState.UNKNOWN

    declared = IngredientRef(
        "house seasoning blend",
        components=(IngredientRef("sesame seeds"), IngredientRef("paprika")),
    )
    screening = screen(declared and [declared],
                       SensitivityProfile(allergens=("sesame", "milk")))
    assert screening.verdict("sesame").state is AllergenState.CONTAINS
    assert screening.verdict("milk").state is (
        AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED
    )
    assert any("→" in ev.ingredient for ev in screening.verdict("sesame").evidence)


# --- unrecognised and opaque: the hidden-allergen cases ----------------------

@pytest.mark.parametrize("text", [
    "1 cup Brand X Secret Sauce",
    "2 tbsp Nando's peri peri rub",
    "1 sachet taco seasoning",
    "1 scoop protein powder",
    "1 tbsp vegan cheese",
    "1 flibbertigibbet",
])
def test_an_unrecognised_or_opaque_ingredient_is_unknown_not_clear(text):
    screening = screen_text(text, EVERYTHING)
    for v in screening.verdicts:
        assert v.state is AllergenState.UNKNOWN, (text, v.allergen)
        assert v.permitted_under(AvoidancePolicy.STRICT) is False
    assert screening.fully_screened is False


def test_unknown_names_the_thing_it_could_not_screen():
    v = verdict_for("1 cup Brand X Secret Sauce", "milk")
    notes = " ".join(ev.note for ev in v.evidence)
    assert "sauce" in notes
    assert "brand" in notes or "secret" in notes
    assert all(ev.provenance.confidence == 0.0 for ev in v.evidence)


def test_a_substitution_hidden_in_an_opaque_product_does_not_read_as_clear():
    # The §14 case: a recipe where the allergen is inside a branded product.
    recipe = [
        IngredientRef("200g rice noodles"),
        IngredientRef("1 tbsp Brand Y stir fry sauce"),  # soy + wheat, undeclared
        IngredientRef("1 clove garlic"),
    ]
    screening = screen(recipe, SensitivityProfile(allergens=("soybeans", "wheat")))
    assert screening.verdict("soybeans").state is AllergenState.UNKNOWN
    assert screening.decide(AvoidancePolicy.STRICT).allowed is False


# --- policies -----------------------------------------------------------------

def test_strict_policy_excludes_unknown():
    """THE rule that protects someone with a real allergy. Mutation-checked."""
    v = verdict_for("1 cup Brand X Secret Sauce", "peanuts")
    assert v.state is AllergenState.UNKNOWN
    assert v.permitted_under(AvoidancePolicy.STRICT) is False


def test_preference_policy_passes_unknown_but_flags_it():
    screening = screen_text("1 cup Brand X Secret Sauce",
                            SensitivityProfile(allergens=("peanuts",)))
    decision = screening.decide(AvoidancePolicy.PREFERENCE)
    assert decision.allowed is True
    assert [v.allergen for v in decision.flagged] == ["peanuts"]
    assert decision.blocking == ()


def test_a_profile_defaults_to_the_strict_policy():
    assert SensitivityProfile().policy is AvoidancePolicy.STRICT
    assert SensitivityProfile.us_major_allergens().policy is AvoidancePolicy.STRICT


# --- sensitivity profiles and custom exclusions ------------------------------

def test_non_major_sensitivity_groups_are_screenable():
    assert state_for("250ml red wine", "sulfites") is AllergenState.MAY_CONTAIN
    assert state_for("200g mussels", "molluscs") is AllergenState.CONTAINS
    assert state_for("500ml beer", "gluten") is AllergenState.CONTAINS


def test_a_custom_exclusion_is_screened_and_recorded_in_the_version():
    profile = SensitivityProfile(allergens=(), custom_exclusions=("coriander",))
    screening = screen(["1 tbsp chopped coriander", "1 onion"], profile)
    v = screening.verdicts[0]
    assert v.allergen == "custom:coriander"
    assert v.label == "coriander"
    assert v.state is AllergenState.CONTAINS
    assert "custom(coriander)" in screening.ruleset_version
    assert v.permitted_under(AvoidancePolicy.STRICT) is False


def test_a_custom_exclusion_not_present_is_the_fourth_state_not_safe():
    profile = SensitivityProfile(allergens=(), custom_exclusions=("coriander",))
    v = screen(["1 onion", "1 tsp salt"], profile).verdicts[0]
    assert v.state is AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED
    assert "not a safety claim" in v.evidence[0].note


def test_an_unknown_allergen_name_is_rejected_not_ignored():
    with pytest.raises(ValueError, match="unknown allergen"):
        resolve_allergen_key("unobtainium")
    with pytest.raises(ValueError):
        screen(["1 onion"], SensitivityProfile(allergens=("unobtainium",)))


@pytest.mark.parametrize("name,key", [
    ("dairy", "milk"), ("Milk", "milk"), ("tree nuts", "tree_nuts"),
    ("nuts", "tree_nuts"), ("soy", "soybeans"), ("shellfish",
     "crustacean_shellfish"), ("sulphites", "sulfites"), ("eggs", "egg"),
])
def test_allergen_names_people_actually_write_resolve(name, key):
    assert resolve_allergen_key(name) == key


# --- the two acceptance targets ----------------------------------------------

# (ingredient line, allergen that must be excluded). Drawn from the spec's §14
# edge cases plus the alias families the engine claims to handle.
KNOWN_EXCLUDED: tuple[tuple[str, str], ...] = (
    ("200ml whole milk", "milk"),
    ("casein", "milk"),
    ("whey powder", "milk"),
    ("50g parmesan", "milk"),
    ("2 large eggs", "egg"),
    ("1 tbsp mayonnaise", "egg"),
    ("2 tbsp fish sauce", "fish"),
    ("4 anchovy fillets", "fish"),
    ("200g prawns", "crustacean_shellfish"),
    ("50g flaked almonds", "tree_nuts"),
    ("marzipan", "tree_nuts"),
    ("almond milk", "tree_nuts"),
    ("2 tbsp peanut butter", "peanuts"),
    ("arachis oil", "peanuts"),
    ("200g semolina", "wheat"),
    ("100g panko", "wheat"),
    ("200g firm tofu", "soybeans"),
    ("2 tbsp soy sauce", "soybeans"),
    ("2 tbsp tahini", "sesame"),
    ("200g hummus", "sesame"),
    ("1 tbsp sesame oil", "sesame"),
    # uncertainty, which a hard constraint must also exclude
    ("100g dark chocolate", "milk"),
    ("gluten free bread", "wheat"),
    ("1 cup Brand X Secret Sauce", "peanuts"),
)


@pytest.mark.parametrize("text,allergen", KNOWN_EXCLUDED)
def test_acceptance_no_known_excluded_allergen_passes_a_hard_constraint(text, allergen):
    """Acceptance target 1: nothing on this list may pass a strict profile."""
    screening = screen_text(text, SensitivityProfile(allergens=(allergen,)))
    decision = screening.decide(AvoidancePolicy.STRICT)
    assert decision.allowed is False, screening.explain()
    assert decision.blocking[0].evidence


UNKNOWN_PRODUCING = (
    "1 cup Brand X Secret Sauce",
    "1 sachet taco seasoning",
    "2 tbsp mystery marinade",
    "1 flibbertigibbet",
    "1 portion grandma's special mix",
)


@pytest.mark.parametrize("text", UNKNOWN_PRODUCING)
def test_acceptance_unknown_is_never_automatically_safe(text):
    """Acceptance target 2: `unknown` is excluded under a hard constraint, for
    every allergen, with evidence saying what could not be screened."""
    screening = screen_text(text, EVERYTHING)
    decision = screening.decide(AvoidancePolicy.STRICT)
    assert decision.allowed is False
    assert len(decision.blocking) == len(ALL_KEYS)
    for v in screening.verdicts:
        assert v.state is AllergenState.UNKNOWN
        assert v.evidence
        assert v.permitted_under(AvoidancePolicy.STRICT) is False


def test_an_empty_recipe_is_the_fourth_state_not_a_clearance():
    screening = screen([], SensitivityProfile(allergens=("milk",)))
    v = screening.verdict("milk")
    assert v.state is AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED
    assert "not a safety claim" in v.evidence[0].note
    # It is "allowed" under both policies, which is why the wording of the
    # fourth state — and the evidence — has to carry the caveat.
    assert screening.decide(AvoidancePolicy.STRICT).allowed is True


# --- rule data integrity -----------------------------------------------------

def test_the_ruleset_is_versioned_and_licensed():
    rs = allergen_ruleset()
    assert rs.version.startswith("allergens/")
    assert rs.license and rs.retrieved and rs.source
    assert len(rs.major) == 9


def test_an_unknown_ruleset_version_is_refused():
    with pytest.raises(ValueError, match="unknown allergen ruleset"):
        screen(["1 onion"], ruleset_version="allergens/1999.1.1")
