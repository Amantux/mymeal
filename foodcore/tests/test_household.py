"""The curated vocabulary that makes a bare cooking word resolvable.

These tests exist because the feature was green against a 37-food fixture and
useless against a real 7,793-food import: "2 cups flour" refused as
`ambiguous_food` because USDA genuinely contains arrowroot, carob, millet,
potato and barley malt flour. Correct about the data, wrong about the question.
"""
from foodcore.rules.household_v1 import (
    CONFIDENCE,
    HOUSEHOLD_FOODS,
    VERSION,
    household_note,
    household_source_id,
)


def test_bare_cooking_words_name_a_specific_food():
    assert household_source_id("flour") == "168894"
    assert household_source_id("Flour") == "168894"       # case-insensitive
    assert household_source_id("  flour  ") == "168894"   # and whitespace


def test_a_word_outside_the_convention_gets_no_default():
    """MUTATION GUARD. The table must not become a fuzzy matcher: a near-miss
    here silently converts the WRONG food, which is the failure this module
    exists to prevent rather than cause."""
    for text in ("arrowroot flour", "barley malt flour", "quinoa", ""):
        assert household_source_id(text) is None


def test_every_entry_explains_itself():
    """An uncited convention is indistinguishable from a guess. Each row names
    the food it resolves to and why, so a reviewer can check the claim without
    running anything."""
    for word, (fdc_id, description, why) in HOUSEHOLD_FOODS.items():
        assert fdc_id.isdigit(), f"{word}: {fdc_id!r} is not an FDC id"
        assert description.strip(), f"{word}: no description"
        # Deliberately a floor, not a prose-quality judgement: "exact" and
        # "British name" are complete justifications, and an earlier version of
        # this assertion failed them for being short, which measures the wrong
        # thing. What it must catch is an EMPTY or placeholder reason.
        assert len(why.strip()) >= 5, f"{word}: no reason given — {why!r}"
        assert why.strip().lower() not in {"todo", "tbd", "n/a", "?"}, word
        assert word == word.lower().strip(), f"{word!r} is not normalised"


def test_the_note_names_the_food_and_the_reason():
    note = household_note("cornflour")

    assert "Cornstarch" in note
    assert "false friend" in note     # the reason, not just the mapping


def test_the_convention_is_weaker_than_a_direct_match():
    """The WEIGHT is measured; the IDENTIFICATION is a cultural convention, and
    a chain is only as strong as its weakest link."""
    assert CONFIDENCE < 0.8
    assert VERSION.startswith("household/")


def test_raw_and_cooked_stay_separate_foods():
    """FR-01's rule survives the convention layer: these are different foods
    with different weights per cup (185 g vs 158 g), not two names for one."""
    assert household_source_id("rice") != household_source_id("cooked rice")
