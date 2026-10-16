"""Tests for the deterministic nutrition arithmetic.

Expected values in the end-to-end test are **derived in the test body** from
the fixture constants, not copied out of a run of the implementation — so the
test would still catch an arithmetic change that this module agreed with
itself about.

Per-100 g figures below are real USDA FoodData Central values (CC0), rounded;
they are fixtures, and the tests never depend on their exact provenance
strings beyond what they assert.
"""
import math

import pytest

from app.nutrition import (
    ALGORITHM_VERSION,
    BASIS_COOKED,
    BASIS_RAW,
    BASIS_UNSPECIFIED,
    DEFAULT_MIN_COMPLETENESS,
    NUTRIENTS,
    Composition,
    ConversionFactor,
    Nutrient,
    NutrientAmount,
    RecipeInput,
    RecipeLine,
    compute_recipe,
    evaluate_line,
    nutrient_ids,
    register_nutrient,
    weakest_method,
)
from app.provenance import Provenance, Resolved, Unresolved

# --------------------------------------------------------------------------
# Fixtures: provenance helpers
# --------------------------------------------------------------------------


def prov(
    source="usda",
    source_id="0",
    retrieved="2026-01-01T00:00:00Z",
    method="measured",
    version="FDC-2025-04",
    confidence=1.0,
    license="CC0-1.0",
):
    return Provenance(source, source_id, retrieved, method, version, confidence, license)


def grams(value, method="measured", confidence=1.0, unit="g", **kw):
    return Resolved(value, unit, prov(source="convert", method=method, confidence=confidence, **kw))


def composition(food_id, description, basis, per_100g, **kw):
    return Composition(
        food_id=food_id,
        description=description,
        basis=basis,
        per_100g={k: NutrientAmount(*v) for k, v in per_100g.items()},
        provenance=prov(source_id=food_id, **kw),
    )


# --------------------------------------------------------------------------
# Fixtures: foods (USDA per 100 g)
# --------------------------------------------------------------------------
CHICKEN = {
    "calories": (120.0, "kcal"),
    "protein": (22.5, "g"),
    "fat": (2.62, "g"),
    "carbs": (0.0, "g"),
    "fiber": (0.0, "g"),
    "sodium": (45.0, "mg"),
}
RICE_COOKED = {
    "calories": (130.0, "kcal"),
    "protein": (2.69, "g"),
    "fat": (0.28, "g"),
    "carbs": (28.17, "g"),
    "fiber": (0.4, "g"),
    "sugar": (0.05, "g"),
    "sodium": (1.0, "mg"),
    "thiamin": (0.02, "mg"),
}
RICE_RAW = {
    "calories": (365.0, "kcal"),
    "protein": (7.13, "g"),
    "fat": (0.66, "g"),
    "carbs": (79.95, "g"),
    "fiber": (1.3, "g"),
    "sodium": (5.0, "mg"),
    "thiamin": (0.07, "mg"),
}
OLIVE_OIL = {
    "calories": (884.0, "kcal"),
    "protein": (0.0, "g"),
    "fat": (100.0, "g"),
    "carbs": (0.0, "g"),
    "fiber": (0.0, "g"),
    "sodium": (2.0, "mg"),
}

# USDA cooking-yield for long-grain white rice, boiled: ~2.8 g cooked per g raw.
RICE_YIELD = 2.8


def chicken_line(g=400.0, **kw):
    return RecipeLine(
        label="chicken breast",
        edible_grams=grams(g, **kw),
        composition=composition("171077", "Chicken, breast, raw", BASIS_RAW, CHICKEN),
        used_as=BASIS_RAW,
    )


def rice_cooked_line(g=500.0):
    return RecipeLine(
        label="cooked rice",
        edible_grams=grams(g),
        composition=composition("169756", "Rice, white, cooked", BASIS_COOKED, RICE_COOKED),
        used_as=BASIS_COOKED,
    )


def oil_line(g=14.0):
    return RecipeLine(
        label="olive oil",
        edible_grams=grams(g, method="estimated", confidence=0.7),
        composition=composition("171413", "Oil, olive", BASIS_UNSPECIFIED, OLIVE_OIL),
        used_as=BASIS_UNSPECIFIED,
    )


# --------------------------------------------------------------------------
# FR-05 arithmetic
# --------------------------------------------------------------------------


def test_ingredient_nutrient_is_grams_over_100_times_per_100g():
    out = evaluate_line(chicken_line(250.0))
    assert out.blocked is None
    # (250 / 100) x 22.5 g protein
    assert out.values["protein"].value == pytest.approx(2.5 * 22.5)
    assert out.values["protein"].unit == "g"
    assert out.values["calories"].value == pytest.approx(2.5 * 120.0)
    assert out.values["sodium"].unit == "mg"
    assert out.values["sodium"].value == pytest.approx(2.5 * 45.0)


def test_recipe_total_and_per_serving_hand_calculated():
    """A real four-ingredient recipe, end to end, checked by hand-calculation."""
    servings = 4
    recipe = RecipeInput(
        label="chicken and rice",
        servings=servings,
        lines=[
            chicken_line(400.0),
            rice_cooked_line(500.0),
            oil_line(14.0),
            # No composition match: contributes mass, contributes no nutrients.
            RecipeLine(
                label="fresh parsley",
                edible_grams=grams(10.0),
                composition=Unresolved("no_match", "no reference food for 'fresh parsley'"),
            ),
        ],
    )
    result = compute_recipe(recipe)

    # --- mass, by hand -------------------------------------------------
    weighable = 400.0 + 500.0 + 14.0 + 10.0
    assert result.weighable_grams == pytest.approx(weighable)
    assert result.mass_complete is True
    assert result.unweighable == ()

    # --- calories, by hand ---------------------------------------------
    expected_kcal = (
        (400.0 / 100.0) * CHICKEN["calories"][0]
        + (500.0 / 100.0) * RICE_COOKED["calories"][0]
        + (14.0 / 100.0) * OLIVE_OIL["calories"][0]
    )
    kcal = result.per_recipe["calories"]
    assert isinstance(kcal.outcome, Resolved)
    assert kcal.outcome.value == pytest.approx(expected_kcal)
    assert kcal.unit == "kcal"
    # Parsley's 10 g is in the denominator but not the numerator.
    assert kcal.covered_grams == pytest.approx(400.0 + 500.0 + 14.0)
    assert kcal.completeness == pytest.approx((400.0 + 500.0 + 14.0) / weighable)
    assert kcal.missing == ("fresh parsley",)

    # --- protein and sodium, by hand -----------------------------------
    expected_protein = (
        4.0 * CHICKEN["protein"][0] + 5.0 * RICE_COOKED["protein"][0] + 0.14 * 0.0
    )
    assert result.per_recipe["protein"].outcome.value == pytest.approx(expected_protein)
    expected_sodium = 4.0 * 45.0 + 5.0 * 1.0 + 0.14 * 2.0
    assert result.per_recipe["sodium"].outcome.value == pytest.approx(expected_sodium)

    # --- per serving, by hand ------------------------------------------
    assert result.per_serving["calories"].outcome.value == pytest.approx(
        expected_kcal / servings
    )
    assert result.per_serving["protein"].outcome.value == pytest.approx(
        expected_protein / servings
    )
    # Per-serving keeps the recipe's completeness: dividing changes no coverage.
    assert result.per_serving["calories"].completeness == kcal.completeness


# --------------------------------------------------------------------------
# DESIGN DECISION 1 — missing is not zero
# --------------------------------------------------------------------------


def test_reported_zero_and_unreported_are_different_things():
    """The bug this module exists to kill: 0 meaning both zero and unknown."""
    reports_zero = evaluate_line(chicken_line())
    assert reports_zero.values["fiber"].value == 0.0  # genuinely zero fibre
    assert "fiber" not in reports_zero.unresolved

    no_record = composition("x", "Mystery powder", BASIS_UNSPECIFIED, {"calories": (10.0, "kcal")})
    out = evaluate_line(
        RecipeLine("mystery powder", grams(100.0), no_record), nutrients=["calories", "fiber"]
    )
    assert "fiber" not in out.values
    assert out.unresolved["fiber"].reason == "nutrient_not_reported"


def test_partial_total_is_refused_below_threshold_and_never_sums_unknowns_as_zero():
    """Mutation guard for 'missing is not zero'.

    If unknown ingredients were totalled as zero, every assertion here flips:
    completeness would be 1.0, `missing` would be empty, and `outcome` would be
    a (wrong, biased-low) Resolved number instead of Unresolved.
    """
    recipe = RecipeInput(
        label="mostly unknown",
        servings=2,
        lines=[
            chicken_line(100.0),
            RecipeLine(
                "unidentified sauce",
                grams(400.0),
                Unresolved("no_match", "no reference food"),
            ),
        ],
    )
    total = compute_recipe(recipe).per_recipe["calories"]

    assert isinstance(total.outcome, Unresolved)
    assert total.outcome.reason == "incomplete"
    assert total.value is None, "an incomplete total must not present a number"
    assert total.completeness == pytest.approx(100.0 / 500.0)
    assert total.covered_grams == pytest.approx(100.0)
    assert total.missing == ("unidentified sauce",)
    # The biased-low sum is available for diagnostics, clearly labelled.
    assert total.partial_value == pytest.approx(120.0)
    # ...and it is NOT what a caller reading `value` gets.
    assert total.value != total.partial_value


def test_completeness_is_mass_weighted_not_count_weighted():
    """One missing heavy ingredient must outweigh many missing light ones."""
    heavy_missing = RecipeInput(
        "a",
        1,
        [
            chicken_line(50.0),
            RecipeLine("stock", grams(950.0), Unresolved("no_match", "x")),
        ],
    )
    many_light_missing = RecipeInput(
        "b",
        1,
        [chicken_line(950.0)]
        + [
            RecipeLine(f"spice {i}", grams(2.0), Unresolved("no_match", "x"))
            for i in range(25)
        ],
    )
    assert compute_recipe(heavy_missing).per_recipe["calories"].completeness == pytest.approx(0.05)
    light = compute_recipe(many_light_missing).per_recipe["calories"]
    assert light.completeness == pytest.approx(950.0 / 1000.0)
    assert isinstance(light.outcome, Resolved)  # 25 of 26 lines missing, still fine


def test_completeness_is_per_nutrient_not_per_recipe():
    recipe = RecipeInput("x", 2, [chicken_line(400.0), rice_cooked_line(500.0)])
    result = compute_recipe(recipe)
    # Both report calories; only the rice reports sugar.
    assert result.per_recipe["calories"].completeness == pytest.approx(1.0)
    assert result.per_recipe["sugar"].completeness == pytest.approx(500.0 / 900.0)
    assert isinstance(result.per_recipe["calories"].outcome, Resolved)
    assert isinstance(result.per_recipe["sugar"].outcome, Unresolved)


def test_threshold_is_a_parameter():
    recipe = RecipeInput("x", 2, [chicken_line(400.0), rice_cooked_line(500.0)])
    lenient = compute_recipe(recipe, min_completeness=0.5).per_recipe["sugar"]
    assert isinstance(lenient.outcome, Resolved)
    assert lenient.outcome.value == pytest.approx(5.0 * RICE_COOKED["sugar"][0])
    # The completeness figure travels with the number so it can be shown.
    assert lenient.completeness == pytest.approx(500.0 / 900.0)
    assert DEFAULT_MIN_COMPLETENESS == 0.80


def test_unweighable_line_is_reported_and_penalises_confidence():
    weighable_only = compute_recipe(RecipeInput("x", 1, [chicken_line(400.0)]))
    with_salt = compute_recipe(
        RecipeInput(
            "x",
            1,
            [
                chicken_line(400.0),
                RecipeLine("salt to taste", Unresolved("no_quantity", "'to taste'"),
                           composition("salt", "Salt", BASIS_UNSPECIFIED, {"sodium": (38758.0, "mg")})),
            ],
        )
    )
    assert with_salt.mass_complete is False
    assert with_salt.unweighable == ("salt to taste",)
    # Mass coverage cannot see it, so confidence must: 1 of 2 lines weighable.
    bare = weighable_only.per_recipe["sodium"].outcome.provenance.confidence
    salted = with_salt.per_recipe["sodium"].outcome.provenance.confidence
    assert salted == pytest.approx(bare * 0.5)
    # And the refusal message names the lines we could not weigh.
    sugar_like = compute_recipe(
        RecipeInput(
            "x",
            1,
            [
                chicken_line(100.0),
                RecipeLine("mystery", grams(400.0), Unresolved("no_match", "x")),
                RecipeLine("salt to taste", Unresolved("no_quantity", "'to taste'"),
                           Unresolved("no_match", "x")),
            ],
        )
    ).per_recipe["calories"]
    assert "salt to taste" in sugar_like.outcome.detail


def test_recipe_with_no_weighable_mass_resolves_nothing():
    recipe = RecipeInput(
        "x",
        2,
        [
            RecipeLine(
                "chicken, some",
                Unresolved("no_quantity", "no amount given"),
                composition("171077", "Chicken", BASIS_RAW, CHICKEN),
            )
        ],
    )
    total = compute_recipe(recipe).per_recipe["calories"]
    assert isinstance(total.outcome, Unresolved)
    assert total.outcome.reason == "no_weighable_mass"
    assert total.completeness == 0.0


# --------------------------------------------------------------------------
# DESIGN DECISION 2 — uncertainty propagation
# --------------------------------------------------------------------------


def test_estimated_grams_cannot_yield_measured_nutrition():
    line = RecipeLine(
        label="3 medium onions",
        edible_grams=grams(330.0, method="estimated", confidence=0.6),
        composition=composition(
            "170000", "Onions, raw", BASIS_RAW, {"calories": (40.0, "kcal")},
            method="measured", confidence=0.95,
        ),
        used_as=BASIS_RAW,
    )
    out = evaluate_line(line)
    p = out.values["calories"].provenance
    assert p.method == "estimated"  # weakest link wins
    assert p.confidence == pytest.approx(0.6 * 0.95)  # chain -> product


def test_measured_inputs_still_only_produce_calculated():
    out = evaluate_line(chicken_line(100.0, method="measured"))
    assert out.values["calories"].provenance.method == "calculated"
    assert weakest_method("measured", "declared") == "declared"
    assert weakest_method("calculated", "measured") == "calculated"
    assert weakest_method("nonsense") == "estimated"  # unknown ranks weakest


def test_total_confidence_is_mass_weighted_mean_of_contributors():
    # 900 g at confidence 1.0, 100 g at confidence 0.5 -> 0.95
    recipe = RecipeInput(
        "x",
        1,
        [
            chicken_line(900.0, confidence=1.0),
            RecipeLine(
                "oil",
                grams(100.0, confidence=0.5),
                composition("171413", "Oil", BASIS_UNSPECIFIED, OLIVE_OIL),
            ),
        ],
    )
    total = compute_recipe(recipe).per_recipe["calories"]
    assert total.outcome.provenance.confidence == pytest.approx(
        (900.0 * 1.0 + 100.0 * 0.5) / 1000.0
    )


def test_total_provenance_takes_oldest_retrieval_and_joins_licences():
    fresh = RecipeLine(
        "chicken",
        grams(100.0),
        composition(
            "171077", "Chicken", BASIS_RAW, CHICKEN,
            retrieved="2026-06-01T00:00:00Z", license="CC0-1.0",
        ),
        used_as=BASIS_RAW,
    )
    stale = RecipeLine(
        "oil",
        grams(100.0),
        composition(
            "171413", "Oil", BASIS_UNSPECIFIED, OLIVE_OIL,
            retrieved="2024-02-01T00:00:00Z", license="ODbL-1.0",
        ),
    )
    p = compute_recipe(RecipeInput("x", 1, [fresh, stale])).per_recipe["calories"].outcome.provenance
    assert p.retrieved == "2024-02-01T00:00:00Z"  # as fresh as the stalest input
    assert p.license == "CC0-1.0 AND ODbL-1.0"  # obligations compose
    assert p.source == "foodcore.nutrition"
    assert p.version.endswith(ALGORITHM_VERSION)
    assert p.version.count(ALGORITHM_VERSION) == 1


# --------------------------------------------------------------------------
# DESIGN DECISION 3 — raw vs cooked (regression fixture)
# --------------------------------------------------------------------------


def test_raw_rice_composition_is_refused_for_cooked_rice():
    """The factor-of-three error: 365 kcal/100 g raw vs 130 cooked."""
    line = RecipeLine(
        label="cooked rice",
        edible_grams=grams(300.0),
        composition=composition("169715", "Rice, white, raw", BASIS_RAW, RICE_RAW),
        used_as=BASIS_COOKED,
    )
    out = evaluate_line(line)
    assert out.values == {}
    assert out.blocked is not None
    assert out.blocked.reason == "basis_mismatch"
    assert "no sourced yield" in out.blocked.detail
    # It would have been 3 x too high.
    assert 3.0 * RICE_RAW["calories"][0] > 2.5 * 3.0 * RICE_COOKED["calories"][0]


def test_better_matching_cooked_record_is_used_directly():
    out = evaluate_line(rice_cooked_line(300.0))
    assert out.values["calories"].value == pytest.approx(3.0 * RICE_COOKED["calories"][0])
    assert out.values["calories"].provenance.method == "calculated"


def test_sourced_yield_factor_bridges_raw_record_to_cooked_use():
    factor = ConversionFactor(
        from_basis=BASIS_RAW,
        to_basis=BASIS_COOKED,
        yield_ratio=RICE_YIELD,
        provenance=prov(source="usda-yield", source_id="rice-boiled", method="declared",
                        confidence=0.8),
        retention={"thiamin": 0.75},  # water-soluble vitamin, lost to the pot
    )
    line = RecipeLine(
        label="cooked rice",
        edible_grams=grams(300.0),
        composition=composition("169715", "Rice, white, raw", BASIS_RAW, RICE_RAW),
        used_as=BASIS_COOKED,
        conversion=factor,
    )
    out = evaluate_line(line)

    raw_equivalent = 300.0 / RICE_YIELD
    assert out.values["calories"].value == pytest.approx(
        (raw_equivalent / 100.0) * RICE_RAW["calories"][0]
    )
    # Sanity: the bridged figure lands close to the dedicated cooked record.
    direct = evaluate_line(rice_cooked_line(300.0)).values["calories"].value
    assert out.values["calories"].value == pytest.approx(direct, rel=0.02)

    # Retention applies per nutrient; macros default to 1.0.
    assert out.values["thiamin"].value == pytest.approx(
        (raw_equivalent / 100.0) * RICE_RAW["thiamin"][0] * 0.75
    )
    assert out.values["protein"].value == pytest.approx(
        (raw_equivalent / 100.0) * RICE_RAW["protein"][0]
    )
    # The factor is part of the chain: 1.0 grams x 1.0 record x 0.8 factor.
    p = out.values["calories"].provenance
    assert p.confidence == pytest.approx(0.8)
    assert p.method == "calculated"  # weakest of calculated/measured/declared


def test_factor_for_the_wrong_bases_is_refused_not_applied():
    factor = ConversionFactor(
        from_basis=BASIS_COOKED,
        to_basis=BASIS_RAW,
        yield_ratio=RICE_YIELD,
        provenance=prov(source="usda-yield"),
    )
    line = RecipeLine(
        "cooked rice",
        grams(300.0),
        composition("169715", "Rice, raw", BASIS_RAW, RICE_RAW),
        used_as=BASIS_COOKED,
        conversion=factor,
    )
    out = evaluate_line(line)
    assert out.values == {}
    assert out.blocked.reason == "factor_mismatch"


def test_unspecified_basis_is_usable_but_only_inferred():
    out = evaluate_line(oil_line(10.0))
    assert out.values["calories"].value == pytest.approx(0.1 * OLIVE_OIL["calories"][0])
    # Grams were estimated here, which is weaker still.
    assert out.values["calories"].provenance.method == "estimated"

    measured_grams = RecipeLine(
        "olive oil",
        grams(10.0, method="measured"),
        composition("171413", "Oil, olive", BASIS_UNSPECIFIED, OLIVE_OIL),
        used_as=BASIS_COOKED,
    )
    assert evaluate_line(measured_grams).values["calories"].provenance.method == "inferred"


# --------------------------------------------------------------------------
# Nutrient coverage and units
# --------------------------------------------------------------------------


def test_nutrient_universe_is_not_myMeals_seven_fields():
    legacy = {"calories", "protein", "carbs", "fat", "fiber", "sugar", "sodium"}
    assert legacy < set(NUTRIENTS)
    assert "saturated_fat" in NUTRIENTS
    for extra in ("vitamin_c", "iron", "folate", "vitamin_b12", "calcium", "potassium"):
        assert extra in NUTRIENTS


def test_unregistered_nutrient_is_still_totalled_in_its_own_unit():
    comp = composition(
        "x", "Seaweed", BASIS_RAW, {"iodine": (1200.0, "µg"), "calories": (45.0, "kcal")}
    )
    recipe = RecipeInput("x", 1, [RecipeLine("seaweed", grams(50.0), comp, used_as=BASIS_RAW)])
    assert "iodine" in nutrient_ids(recipe)
    total = compute_recipe(recipe).per_recipe["iodine"]
    assert total.unit == "µg"
    assert total.outcome.value == pytest.approx(0.5 * 1200.0)


def test_register_nutrient_sets_the_canonical_reporting_unit():
    register_nutrient(Nutrient("iodine_test", "Iodine", "mg"))
    try:
        comp = composition("x", "Seaweed", BASIS_RAW, {"iodine_test": (1200.0, "µg")})
        out = evaluate_line(RecipeLine("seaweed", grams(100.0), comp, used_as=BASIS_RAW))
        assert out.values["iodine_test"].unit == "mg"
        assert out.values["iodine_test"].value == pytest.approx(1.2)
    finally:
        del NUTRIENTS["iodine_test"]
    with pytest.raises(ValueError):
        register_nutrient(Nutrient("bad", "Bad", "furlongs"))


def test_source_units_are_converted_to_the_canonical_unit():
    comp = composition("x", "Broth", BASIS_UNSPECIFIED, {"sodium": (0.5, "g")})
    out = evaluate_line(RecipeLine("broth", grams(200.0), comp))
    assert out.values["sodium"].unit == "mg"
    assert out.values["sodium"].value == pytest.approx(2.0 * 500.0)


def test_energy_in_kilojoules_is_converted_not_rejected():
    comp = composition("x", "Biscuit", BASIS_UNSPECIFIED, {"calories": (2000.0, "kJ")})
    out = evaluate_line(RecipeLine("biscuit", grams(100.0), comp))
    assert out.values["calories"].unit == "kcal"
    assert out.values["calories"].value == pytest.approx(2000.0 / 4.184)


def test_incommensurable_nutrient_unit_is_unresolved_not_guessed():
    register_nutrient(Nutrient("weird", "Weird", "kcal"))
    try:
        comp = composition("x", "Thing", BASIS_UNSPECIFIED, {"weird": (5.0, "mg")})
        out = evaluate_line(RecipeLine("thing", grams(100.0), comp))
        assert "weird" not in out.values
        assert out.unresolved["weird"].reason == "unit_conflict"
    finally:
        del NUTRIENTS["weird"]


def test_calorie_is_rejected_as_an_ambiguous_unit():
    with pytest.raises(ValueError):
        NutrientAmount(100.0, "cal")


# --------------------------------------------------------------------------
# Serving arithmetic
# --------------------------------------------------------------------------


@pytest.mark.parametrize("servings", [0, None, -2])
def test_bad_serving_count_is_unresolved_not_a_division(servings):
    recipe = RecipeInput("x", servings, [chicken_line(400.0)])
    result = compute_recipe(recipe)
    # The recipe total is still perfectly good...
    assert isinstance(result.per_recipe["calories"].outcome, Resolved)
    # ...but no serving count is invented (the LLM path assumed 4).
    per_serving = result.per_serving["calories"]
    assert isinstance(per_serving.outcome, Unresolved)
    assert per_serving.outcome.reason == "servings_unknown"
    assert per_serving.value is None
    assert per_serving.partial_value is None


def test_servings_one_is_identity():
    recipe = RecipeInput("x", 1, [chicken_line(400.0)])
    result = compute_recipe(recipe)
    assert result.per_serving["calories"].outcome.value == pytest.approx(
        result.per_recipe["calories"].outcome.value
    )


def test_per_serving_of_an_incomplete_total_stays_unresolved():
    recipe = RecipeInput(
        "x", 4,
        [chicken_line(100.0), RecipeLine("mystery", grams(900.0), Unresolved("no_match", "x"))],
    )
    per_serving = compute_recipe(recipe).per_serving["calories"]
    assert isinstance(per_serving.outcome, Unresolved)
    assert per_serving.outcome.reason == "incomplete"
    assert per_serving.partial_value == pytest.approx(120.0 / 4)


# --------------------------------------------------------------------------
# Input contract
# --------------------------------------------------------------------------


def test_absent_data_must_be_stated_not_passed_as_none():
    comp = composition("x", "Chicken", BASIS_RAW, CHICKEN)
    with pytest.raises(TypeError):
        RecipeLine("chicken", None, comp)
    with pytest.raises(TypeError):
        RecipeLine("chicken", grams(100.0), None)
    with pytest.raises(TypeError):
        RecipeLine("chicken", grams(100.0), {"calories": 120})
    with pytest.raises(TypeError):
        RecipeInput("x", 1, [{"label": "chicken"}])
    with pytest.raises(ValueError):
        RecipeLine("chicken", grams(100.0), comp, used_as="al dente")


def test_grams_must_actually_be_grams():
    comp = composition("x", "Chicken", BASIS_RAW, CHICKEN)
    out = evaluate_line(RecipeLine("chicken", Resolved(2.0, "cup", prov()), comp, used_as=BASIS_RAW))
    assert out.blocked.reason == "grams_unit"
    for bad in (-1.0, math.nan, math.inf):
        out = evaluate_line(RecipeLine("chicken", Resolved(bad, "g", prov()), comp, used_as=BASIS_RAW))
        assert out.blocked.reason == "grams_invalid"


def test_composition_rejects_loose_values_and_unknown_bases():
    with pytest.raises(TypeError):
        Composition("x", "d", BASIS_RAW, {"calories": 120.0}, prov())
    with pytest.raises(ValueError):
        Composition("x", "d", "steamed", {}, prov())
    with pytest.raises(ValueError):
        ConversionFactor(BASIS_RAW, BASIS_COOKED, 0.0, prov())


def test_computation_is_deterministic_and_pure():
    recipe = RecipeInput(
        "x", 3, [chicken_line(400.0), rice_cooked_line(500.0), oil_line(14.0)]
    )
    first = compute_recipe(recipe)
    second = compute_recipe(recipe)
    assert first == second
    assert first.per_recipe["calories"].outcome == second.per_recipe["calories"].outcome
