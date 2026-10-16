"""The adapter's job: turn the published CSV schema into usable portion weights.

Every expected number here was read out of the real USDA export, not invented.

Two fixtures, two concerns. `export` is the PARSE result and is what the tests
about reading USDA's schema assert against. `dataset` is a real built dataset
file, and is what the tests about *resolution policy* use — that policy moved
into the one `Dataset` when the two Dataset classes were collapsed, and it is
only honest to test it where it now lives.
"""
import os

import pytest

from foodcore.sources.usda import (
    NUTRIENT_NAMES,
    UsdaImportError,
    load_fdc_csv,
    parse_portion_text,
)
from foodcore.units import normalise_unit, singularise, unit_kind

from .conftest import FIXTURE_DIR, FIXTURE_RETRIEVED, FIXTURE_VERSION

FLOUR = "168894"        # Wheat flour, white, all-purpose, enriched, bleached
ONION = "170000"        # Onions, raw
RICE_RAW = "168877"     # Rice, white, long-grain, regular, raw, enriched
RICE_COOKED = "168878"  # Rice, white, long-grain, regular, enriched, cooked
GARLIC = "169230"       # Garlic, raw
EGG_WHITE = "172183"    # Egg, white, raw, fresh
TOMATO_CAN = "170051"   # Tomatoes, red, ripe, canned, packed in tomato juice


# --- loading ----------------------------------------------------------------

def test_fixture_is_small_enough_to_commit():
    total = sum(
        os.path.getsize(os.path.join(FIXTURE_DIR, f))
        for f in os.listdir(FIXTURE_DIR)
    )
    assert total < 256 * 1024, "the bulk USDA export must never be committed"


def test_loads_foods_and_portions(export):
    assert len(export.foods) >= 30
    assert export.source == "usda"  # provenance.SOURCES member, not an edition
    assert export.license == "CC0-1.0"
    assert export.version == FIXTURE_VERSION
    assert export.retrieved == FIXTURE_RETRIEVED
    assert export.food(FLOUR).description.startswith("Wheat flour, white, all-purpose")


def test_a_food_key_namespaces_the_provider(export):
    # Two providers may both number a food "1"; the dataset key must not.
    assert export.food(FLOUR).key == f"usda:{FLOUR}"
    assert export.food(FLOUR).provider == "usda"


def test_retrieved_defaults_to_the_datasets_own_publication_date():
    """Not the clock: loading the same export twice must give the same provenance."""
    d1 = load_fdc_csv(FIXTURE_DIR)
    d2 = load_fdc_csv(FIXTURE_DIR)
    assert d1.retrieved == d2.retrieved
    assert d1.retrieved.startswith("20")


def test_version_inferred_from_an_upstream_directory_name():
    from foodcore.sources.usda import infer_version
    assert infer_version(
        "/data/FoodData_Central_sr_legacy_food_csv_2018-04"
    ) == "sr_legacy_food/2018-04"
    assert infer_version("/data/whatever") == "unknown"


def test_a_missing_directory_is_a_typed_error():
    with pytest.raises(UsdaImportError):
        load_fdc_csv("/nonexistent/fdc")


def test_a_directory_that_is_not_an_export_is_a_typed_error(tmp_path):
    with pytest.raises(UsdaImportError) as exc:
        load_fdc_csv(str(tmp_path))

    assert "food.csv" in str(exc.value)


# --- schema trap 1: the unit hides in `modifier` ----------------------------

def test_sr_legacy_unit_is_read_out_of_the_modifier_text():
    """measure_unit_id 9999 is 'undetermined'; the unit is free text."""
    unit, size, qual = parse_portion_text("undetermined", "cup, chopped", "")
    assert (unit, size) == ("cup", None)
    assert qual == "chopped"


def test_foundation_food_unit_is_read_out_of_measure_unit():
    unit, size, qual = parse_portion_text("tablespoon", "", "")
    assert (unit, size, qual) == ("tbsp", None, "")


@pytest.mark.parametrize(("modifier", "unit", "size"), [
    ("medium (2-1/2\" dia)", "piece", "medium"),      # 1 medium onion
    ("large", "piece", "large"),                      # 1 large egg
    ("extra large", "piece", "extra large"),
    ("fruit (2-5/8\" dia)", "piece", None),           # 1 orange
    ("slice, large (1/4\" thick)", "slice", "large"),
    ("stalk, medium (7-1/2\" - 8\" long)", "stalk", "medium"),
    ("cloves", "clove", None),
    ("can (303 x 406)", "can", None),
    ("package (10 oz)", "package", None),
    ("cup (4.86 large eggs)", "cup", None),
    ("tsp unpacked", "tsp", None),
    ("NLEA serving", "serving", None),
    ("bunch", "bunch", None),
])
def test_real_modifier_strings_from_the_export(modifier, unit, size):
    got_unit, got_size, _ = parse_portion_text("undetermined", modifier, "")
    assert (got_unit, got_size) == (unit, size)


def test_unparseable_modifiers_are_kept_but_not_guessed():
    """'lemon yields' means 'the juice of one lemon'. Inventing a unit for it
    would be exactly the fabrication this module refuses."""
    unit, size, _ = parse_portion_text("undetermined", "lemon yields", "")
    assert unit is None and size is None


def test_an_unnameable_portion_is_counted_not_guessed_at(export):
    """The 'lemon yields' rows above reach the loader too. They are skipped —
    and the skip is REPORTED, because a silent drop is indistinguishable from
    an export that never had the row."""
    assert export.stats.skipped_portions > 0
    assert "skipped" in export.stats.report()


# --- schema trap 2: `amount` is not always 1 --------------------------------

def test_grams_per_unit_divides_by_amount(export):
    """'3 cloves = 9 g' is 3 g per clove, not 9."""
    garlic = export.food(GARLIC)
    rows = [p for p in garlic.portions if p.measure == "clove"]
    assert {round(p.grams_per_unit, 3) for p in rows} == {3.0}
    assert any(p.amount == 3 and p.gram_weight == 9 for p in rows)


def test_the_upstream_amount_survives_into_the_dataset_file(dataset):
    """MUTATION GUARD for the storage layer.

    `amount` is not a parse-time curiosity to be divided away on the way in:
    a reader must be able to see that upstream published "3 cloves = 9 g". The
    importer that was deleted stored only `grams/amount` and lost it, which
    also meant nothing downstream could tell a per-unit figure from a published
    one. If this goes red, the column was dropped.
    """
    garlic = dataset.food_record(f"usda:{GARLIC}")
    rows = [p for p in garlic.portions if p.measure == "clove"]

    assert any(p.amount == 3 and p.gram_weight == 9 for p in rows)
    assert {round(p.grams_per_unit, 3) for p in rows} == {3.0}


# --- portion selection -------------------------------------------------------

def test_portion_prefers_the_unqualified_row(export):
    """Onions publish 'cup, chopped' 160 g and 'cup, sliced' 115 g; neither is
    the plain cup, so a bare 'cup' must not silently pick one."""
    onion = export.food(ONION)
    assert onion.portion("cup") is None
    flour = export.food(FLOUR)
    assert flour.portion("cup").grams_per_unit == 125.0


def test_size_words_are_first_class(export):
    onion = export.food(ONION)
    assert onion.portion("piece", "small").grams_per_unit == 70.0
    assert onion.portion("piece", "medium").grams_per_unit == 110.0
    assert onion.portion("piece", "large").grams_per_unit == 150.0


def test_portion_selection_is_stable_across_loads():
    a = load_fdc_csv(FIXTURE_DIR)
    b = load_fdc_csv(FIXTURE_DIR)
    for rec in a.foods:
        other = b.food(rec.source_id)
        for measure in ("cup", "tbsp", "piece", "slice"):
            pa, pb = rec.portion(measure), other.portion(measure)
            assert (pa.source_id if pa else None) == (pb.source_id if pb else None)


def test_nutrients_are_loaded_but_not_interpreted(export):
    """Nutrition arithmetic belongs to a sibling module; the adapter only
    carries the per-100 g rows through."""
    flour = export.food(FLOUR)
    assert flour.nutrients, "expected per-100g nutrient rows on the fixture"
    amount, unit = next(iter(flour.nutrients.values()))
    assert isinstance(amount, float) and isinstance(unit, str)


def test_nutrients_are_keyed_by_foodcores_own_vocabulary(export):
    """Not by USDA's numeric id. An engine must be able to ask for "protein"
    without knowing that FDC calls it 1003, and the dataset file stores the
    same key, so the parse and the read agree."""
    flour = export.food(FLOUR)

    assert set(flour.nutrients) <= set(NUTRIENT_NAMES.values())
    assert "protein" in flour.nutrients


def test_both_published_spellings_of_total_sugars_map_to_one_key():
    # SR Legacy says "Sugars, Total"; Foundation says the NLEA-qualified name.
    # Mapping only one silently drops sugar for half the dataset.
    assert NUTRIENT_NAMES["sugars, total"] == "sugars"
    assert NUTRIENT_NAMES["sugars, total including nlea"] == "sugars"


def test_energy_is_stored_in_kcal_not_upstreams_casing(export):
    amount, unit = export.food(FLOUR).nutrients["energy"]

    assert unit == "kcal"
    assert amount > 0


# --- name resolution: raw vs cooked must not merge (spec FR-01) -------------

def test_raw_and_cooked_rice_are_different_foods(export):
    raw, cooked = export.food(RICE_RAW), export.food(RICE_COOKED)
    assert raw.source_id != cooked.source_id
    assert raw.portion("cup").grams_per_unit == 185.0
    assert cooked.portion("cup").grams_per_unit == 158.0
    assert "raw" in raw.states and "cooked" in cooked.states


def test_bare_rice_refuses_to_pick_raw_or_cooked(dataset):
    match = dataset.resolve_food("rice")
    assert not match.confident
    assert match.food is None
    ids = {rec.source_id for rec, _ in match.candidates}
    assert {RICE_RAW, RICE_COOKED} <= ids
    # The refusal must carry what settles it for a human.
    assert "raw" in match.describe() and "cooked" in match.describe()


@pytest.mark.parametrize(("query", "fdc_id"), [
    ("cooked rice", RICE_COOKED),
    ("raw rice", RICE_RAW),
])
def test_naming_the_state_resolves_it(dataset, query, fdc_id):
    match = dataset.resolve_food(query)
    assert match.confident and match.food.source_id == fdc_id


def test_dry_and_cooked_oats_also_stay_apart(dataset):
    assert not dataset.resolve_food("oats").confident
    assert dataset.resolve_food("dry oats").confident
    assert dataset.resolve_food("cooked oats").confident


def test_a_substring_is_not_evidence_of_being_the_same_substance(dataset):
    """myMeal's density table weighed peanut butter as butter because 'butter'
    is a substring. Head-noun scoring is what prevents that."""
    assert dataset.resolve_food("butter").food.description == "Butter, salted"
    pb = dataset.resolve_food("peanut butter")
    assert pb.confident and pb.food.description.startswith("Peanut butter")


def test_a_candidate_must_contain_the_query_as_WHOLE_tokens(dataset):
    """'egg' must never be evidence for 'eggplant'.

    Note on where this is enforced: the SQL prefilter is space-padded
    (`tokens LIKE '% egg %'`) so it narrows on whole tokens, but that is a
    SPEED measure only — the Python re-check below it would reject a substring
    candidate anyway. Mutation-checked 2026-10-16: removing the padding changes
    no result, so this test is deliberately written against the observable
    guarantee rather than pretending to guard the padding.
    """
    for rec, _ in dataset.resolve_food("egg").candidates:
        assert "egg" in rec.tokens, rec.description


def test_unique_full_token_coverage_resolves(dataset):
    assert dataset.resolve_food("egg white").food.source_id == EGG_WHITE
    assert dataset.resolve_food("egg whites").food.source_id == EGG_WHITE


def test_partial_coverage_is_not_a_match(dataset):
    assert dataset.resolve_food("unicorn tenderloin").candidates == ()


def test_sugar_without_a_qualifier_is_ambiguous(dataset):
    assert not dataset.resolve_food("sugar").confident
    assert dataset.resolve_food("brown sugar").confident


def test_resolving_by_fdc_id_is_always_confident(dataset):
    match = dataset.resolve_food(TOMATO_CAN)
    assert match.confident and match.food.source_id == TOMATO_CAN


def test_resolving_by_dataset_key_is_always_confident(dataset):
    match = dataset.resolve_food(f"usda:{TOMATO_CAN}")
    assert match.confident and match.food.source_id == TOMATO_CAN


def test_a_wildcard_in_a_query_is_not_a_wildcard(dataset):
    """`%` and `_` are LIKE metacharacters. Unescaped, '_' would match any
    character and quietly widen every query that contains one."""
    assert dataset.resolve_food("ric_").candidates == ()
    assert dataset.resolve_food("%").candidates == ()


# --- vocabulary --------------------------------------------------------------

@pytest.mark.parametrize(("raw", "canon"), [
    ("Cups", "cup"), ("tablespoons", "tbsp"), ("fl. oz.", "fl oz"),
    ("lbs", "lb"), ("grammes", "g"), ("cloves", "clove"), ("cans", "can"),
    ("fruit", "piece"), ("each", "piece"),
])
def test_unit_spellings_normalise(raw, canon):
    assert normalise_unit(raw) == canon


def test_count_and_package_units_are_not_volumes():
    assert unit_kind("clove") == "count"
    assert unit_kind("can") == "package"
    assert unit_kind("cup") == "volume"
    assert unit_kind("lb") == "mass"
    assert unit_kind("furlong") is None


@pytest.mark.parametrize(("word", "expected"), [
    ("tomatoes", "tomato"), ("onions", "onion"), ("whites", "white"),
    ("berries", "berry"), ("glass", "glass"),
])
def test_singularise(word, expected):
    assert singularise(word) == expected
