"""Dietary engine tests, including the spec's vegetarian-ambiguous Parmesan.

The acceptance shape mirrors the allergen suite: a curated table of
known-excluded pairs that must never pass a strict policy, and a guarantee that
uncertainty and unscreened text are never reported as compatible.
"""
from __future__ import annotations

import pytest

from foodcore.allergens import AllergenState, AvoidancePolicy, SensitivityProfile, screen
from foodcore.dietary import (
    DIET_KEYS,
    CustomDiet,
    DietPolicy,
    DietState,
    DietVerdict,
    IngredientRef,
    screen_diet_text,
    screen_diets,
)
from foodcore.rules import dietary_ruleset


def state_for(items, diet):
    items = [items] if isinstance(items, str) else items
    return screen_diets(items, (diet,)).verdict(diet).state


def verdict_for(items, diet):
    items = [items] if isinstance(items, str) else items
    return screen_diets(items, (diet,)).verdict(diet)


# --- the API shape -----------------------------------------------------------

def test_the_fourth_state_is_not_spelled_compatible():
    assert DietState.NO_CONFLICT_IDENTIFIED.value == "no_conflict_identified"
    assert "not a certification" in str(DietState.NO_CONFLICT_IDENTIFIED)
    # `incompatible` is the only member allowed to contain "compatible", and
    # it is the NEGATIVE state. No member claims compatibility.
    assert DietState.INCOMPATIBLE.value == "incompatible"
    for state in DietState:
        if state is DietState.INCOMPATIBLE:
            continue
        assert not any(w in state.value for w in ("compatible", "safe", "ok"))


def test_a_verdict_has_no_truth_value_and_needs_an_explicit_policy():
    v = verdict_for("50g parmesan", "vegetarian")
    with pytest.raises(TypeError, match="no truth value"):
        bool(v)
    with pytest.raises(TypeError):
        v.permitted_under("strict")
    with pytest.raises(TypeError):
        v.permitted_under()  # type: ignore[call-arg]
    assert v.permitted_under(DietPolicy.STRICT) is False


def test_a_screening_has_no_truth_value():
    with pytest.raises(TypeError, match="no truth value"):
        bool(screen_diet_text("1 onion", ("vegan",)))


def test_a_verdict_without_evidence_is_a_bug():
    with pytest.raises(ValueError, match="no evidence"):
        DietVerdict(diet="vegan", label="Vegan", state=DietState.INCOMPATIBLE,
                    evidence=(), ruleset_version="x")


def test_every_verdict_carries_evidence():
    screening = screen_diets(["1 onion", "1 tbsp olive oil"])
    assert len(screening.verdicts) == len(DIET_KEYS)
    for v in screening.verdicts:
        assert v.evidence, v.diet
        for ev in v.evidence:
            assert ev.rule_id and ev.rule_version
            assert ev.provenance is not None


def test_an_unknown_diet_is_refused():
    with pytest.raises(ValueError, match="unknown diet"):
        screen_diets(["1 onion"], ("fruitarian",))


# --- the spec's own example: Parmesan ----------------------------------------

def test_parmesan_is_not_automatically_vegetarian_compatible():
    """The spec's example. Animal rennet makes this genuinely uncertain, and
    uncertain must never be reported as compatible."""
    v = verdict_for("50g grated parmesan", "vegetarian")
    assert v.state is DietState.UNCERTAIN
    assert any("rennet" in ev.note for ev in v.evidence)
    assert v.permitted_under(DietPolicy.STRICT) is False
    assert v.certified is False


def test_parmesan_is_incompatible_with_vegan():
    v = verdict_for("50g parmesan", "vegan")
    assert v.state is DietState.INCOMPATIBLE
    assert any(ev.match_kind == "allergen_group" for ev in v.evidence)


@pytest.mark.parametrize("text,diet", [
    ("200g cheddar", "vegetarian"),
    ("1 glass white wine", "vegetarian"),
    ("500ml ale", "vegetarian"),
    ("1 sheet puff pastry", "vegan"),
    ("1 tbsp natural flavouring", "vegan"),
    ("500ml vegetable stock", "vegetarian"),
])
def test_genuine_uncertainty_is_reported_as_uncertain(text, diet):
    v = verdict_for(text, diet)
    assert v.state is DietState.UNCERTAIN
    assert all(ev.note for ev in v.evidence)
    assert v.permitted_under(DietPolicy.STRICT) is False
    assert v.permitted_under(DietPolicy.PREFERENCE) is True


# --- the straightforward diets -----------------------------------------------

@pytest.mark.parametrize("text,diet", [
    ("200g chicken breast", "vegan"),
    ("200g chicken breast", "vegetarian"),
    ("200g chicken breast", "pescatarian"),
    ("200g salmon", "vegan"),
    ("200g salmon", "vegetarian"),
    ("200g prawns", "vegetarian"),
    ("2 tbsp fish sauce", "vegetarian"),
    ("1 tsp gelatin", "vegetarian"),
    ("100g marshmallows", "vegetarian"),
    ("1 tbsp lard", "pescatarian"),
    ("1 tbsp honey", "vegan"),
    ("2 large eggs", "vegan"),
    ("200ml milk", "vegan"),
    ("200ml milk", "dairy_free"),
    ("200g semolina", "gluten_free"),
    ("500ml beer", "gluten_free"),
    ("200g spaghetti", "paleo"),
    ("200g lentils", "whole30"),
    ("2 tbsp sugar", "whole30"),
])
def test_known_exclusions_are_identified(text, diet):
    assert state_for(text, diet) is DietState.INCOMPATIBLE


@pytest.mark.parametrize("text,diet", [
    ("200g salmon", "pescatarian"),
    ("1 tbsp honey", "vegetarian"),
    ("2 large eggs", "vegetarian"),
    ("200ml almond milk", "dairy_free"),
    ("200ml almond milk", "vegan"),
    ("200g rice", "gluten_free"),
    ("200g chicken breast", "dairy_free"),
    ("1 onion", "vegan"),
])
def test_things_that_do_not_conflict_report_no_conflict(text, diet):
    assert state_for(text, diet) is DietState.NO_CONFLICT_IDENTIFIED


def test_dairy_free_honours_the_material_boundary_via_the_allergen_engine():
    """"almond milk" is not dairy. The rule lives in the allergen engine, and
    dietary delegates to it rather than keeping a second lexicon."""
    assert state_for("200ml unsweetened almond milk", "dairy_free") is (
        DietState.NO_CONFLICT_IDENTIFIED
    )
    assert state_for("200ml oat milk", "dairy_free") is (
        DietState.NO_CONFLICT_IDENTIFIED
    )
    assert state_for("200ml whole milk", "dairy_free") is DietState.INCOMPATIBLE
    # And the delegation is visible in the evidence trail.
    v = verdict_for("200ml whole milk", "dairy_free")
    assert any("allergen engine" in ev.note for ev in v.evidence)
    assert any("allergens/" in ev.rule_version for ev in v.evidence)


def test_the_delegated_allergen_version_is_recorded():
    screening = screen_diets(["200ml milk"], ("dairy_free",))
    assert screening.allergen_ruleset_version.startswith("allergens/")
    assert screening.ruleset_version.startswith("dietary/")


# --- honesty about programs we cannot verify ---------------------------------

@pytest.mark.parametrize("diet", ["whole30", "paleo", "keto", "low_carb",
                                  "gluten_free"])
def test_advisory_diets_are_never_certified(diet):
    v = verdict_for(["1 onion", "1 tbsp olive oil", "200g chicken breast"], diet)
    assert v.advisory, diet
    assert v.certified is False
    assert "nformational" in v.advisory or "cross-contact" in v.advisory
    assert "informational" in v.explain().lower()


def test_non_advisory_diets_can_be_certified():
    v = verdict_for(["1 onion", "1 tbsp olive oil", "200g rice"], "vegan")
    assert v.state is DietState.NO_CONFLICT_IDENTIFIED
    assert v.advisory is None
    assert v.certified is True


def test_keto_flags_carbohydrate_rather_than_claiming_arithmetic():
    v = verdict_for(["1 banana", "2 tbsp honey"], "keto")
    assert v.state is DietState.UNCERTAIN
    assert "no nutrient data" in v.advisory or "not counted" in v.advisory
    assert v.certified is False


def test_a_decision_separates_blocking_flagged_and_unverified():
    screening = screen_diets(
        ["50g parmesan", "1 tbsp olive oil"],
        ("vegan", "vegetarian", "pescatarian", "whole30"),
    )
    strict = screening.decide(DietPolicy.STRICT)
    assert strict.allowed is False
    assert {v.diet for v in strict.blocking} >= {"vegan", "vegetarian"}

    preference = screening.decide(DietPolicy.PREFERENCE)
    # whole30 blocks too, and correctly: parmesan is dairy, which whole30
    # excludes outright rather than merely questioning.
    assert {v.diet for v in preference.blocking} == {"vegan", "whole30"}
    assert {v.diet for v in preference.flagged} >= {"vegetarian", "pescatarian"}
    with pytest.raises(TypeError):
        screening.decide("strict")


# --- unscreened text ---------------------------------------------------------

def test_an_unrecognised_ingredient_is_unknown_not_compatible():
    screening = screen_diets(["1 cup Brand X Secret Sauce"],
                             ("vegan", "gluten_free", "dairy_free"))
    for v in screening.verdicts:
        assert v.state in (DietState.UNKNOWN, DietState.UNCERTAIN), v.diet
        assert v.permitted_under(DietPolicy.STRICT) is False
        assert v.certified is False
    assert screening.decide(DietPolicy.STRICT).allowed is False


def test_a_flatly_unrecognised_ingredient_is_unknown_for_every_diet():
    screening = screen_diets(["1 flibbertigibbet"])
    for v in screening.verdicts:
        assert v.state is DietState.UNKNOWN, v.diet
        assert any(ev.match_kind == "unscreened_text" for ev in v.evidence)
        assert v.permitted_under(DietPolicy.STRICT) is False


def test_an_uncertain_line_that_is_also_unreadable_keeps_both_reasons():
    v = verdict_for("1 cup Brand X Secret Sauce", "vegan")
    kinds = {ev.match_kind for ev in v.evidence}
    assert "uncertain_class" in kinds
    assert "unscreened_text" in kinds or "allergen_group" in kinds


# --- components and custom diets ---------------------------------------------

def test_a_compound_ingredient_is_screened_through_its_components():
    ing = IngredientRef(
        "house marinade",
        components=(IngredientRef("2 tbsp fish sauce"), IngredientRef("1 clove garlic")),
    )
    v = screen_diets([ing], ("vegetarian",)).verdict("vegetarian")
    assert v.state is DietState.INCOMPATIBLE
    assert any("→" in ev.ingredient for ev in v.evidence)


def test_a_custom_diet_is_screened_and_recorded_in_the_version():
    custom = CustomDiet(key="no_nightshades", label="No nightshades",
                        exclusions=("tomato", "aubergine", "potato"))
    screening = screen_diets(
        ["2 tomatoes", "1 onion"], ("vegan",), custom=custom
    )
    v = screening.verdict("no_nightshades")
    assert v.state is DietState.INCOMPATIBLE
    assert any(ev.match_kind == "custom_exclusion" for ev in v.evidence)
    assert "custom(no_nightshades" in screening.ruleset_version
    assert v.permitted_under(DietPolicy.STRICT) is False


def test_a_custom_diet_with_nothing_matching_is_not_a_certification():
    custom = CustomDiet("no_nightshades", "No nightshades", ("tomato",))
    v = screen_diets(["1 onion"], (), custom=custom).verdict("no_nightshades")
    assert v.state is DietState.NO_CONFLICT_IDENTIFIED
    assert "Not a certification" in v.evidence[0].note


# --- acceptance --------------------------------------------------------------

KNOWN_EXCLUDED: tuple[tuple[tuple[str, ...], str], ...] = (
    (("200g beef mince", "1 onion"), "vegetarian"),
    (("200g beef mince", "1 onion"), "vegan"),
    (("200g beef mince",), "pescatarian"),
    (("1 tsp gelatine",), "vegetarian"),
    (("2 tbsp worcestershire sauce",), "vegetarian"),
    (("50g parmesan",), "vegetarian"),        # uncertain, still excluded
    (("1 glass red wine",), "vegan"),         # uncertain, still excluded
    (("200g spaghetti",), "gluten_free"),
    (("200ml semi skimmed milk",), "dairy_free"),
    (("2 tbsp honey",), "whole30"),
    (("200g white rice",), "paleo"),
    (("1 cup Brand X Secret Sauce",), "vegan"),   # unknown, still excluded
    (("1 flibbertigibbet",), "vegetarian"),       # unknown, still excluded
)


@pytest.mark.parametrize("items,diet", KNOWN_EXCLUDED)
def test_acceptance_nothing_excluded_or_uncertain_passes_a_strict_policy(items, diet):
    screening = screen_diets(list(items), (diet,))
    decision = screening.decide(DietPolicy.STRICT)
    assert decision.allowed is False, screening.explain()
    assert decision.blocking[0].evidence


def test_acceptance_uncertainty_is_never_reported_as_certified():
    """Nothing uncertain, unknown or advisory may claim certification."""
    cases = ["50g parmesan", "1 glass wine", "1 flibbertigibbet",
             "1 sheet puff pastry", "1 banana"]
    for text in cases:
        for v in screen_diet_text(text).verdicts:
            if v.state is not DietState.NO_CONFLICT_IDENTIFIED or v.advisory:
                assert v.certified is False, (text, v.diet)


# --- rule data integrity -----------------------------------------------------

def test_every_soft_class_term_explains_itself():
    """A rule that tells a user "we are not sure" must say why. The loader
    enforces this; this test is what makes the loader's check meaningful."""
    rs = dietary_ruleset()
    soft_terms = [
        term for term, classes in rs.term_classes.items()
        if any(c.startswith("animal_") and c != "animal_byproduct"
               and c != "animal_slaughter_derived" for c in classes)
    ]
    assert soft_terms
    for term in soft_terms:
        assert rs.term_notes.get(term), term


def test_the_ruleset_is_versioned_and_licensed():
    rs = dietary_ruleset()
    assert rs.version.startswith("dietary/")
    assert rs.license and rs.retrieved and rs.source
    assert set(DIET_KEYS) >= {
        "vegan", "vegetarian", "pescatarian", "gluten_free", "dairy_free",
        "keto", "low_carb", "paleo", "whole30",
    }


def test_an_unknown_ruleset_version_is_refused():
    with pytest.raises(ValueError, match="unknown dietary ruleset"):
        screen_diets(["1 onion"], ("vegan",), ruleset_version="dietary/1999.1.1")


def test_the_allergen_engine_is_the_single_material_boundary_authority():
    """Guards against a second lexicon appearing: if dietary ever stops
    delegating, this stays green but the one above it would not."""
    v = screen(["almond milk"], SensitivityProfile(allergens=("milk",))).verdict("milk")
    assert v.state is AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED
    assert v.permitted_under(AvoidancePolicy.PREFERENCE) is True
