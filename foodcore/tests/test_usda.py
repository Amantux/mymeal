"""The adapter's job: turn the published CSV schema into usable portion weights.

Every expected number here was read out of the real USDA export, not invented.
"""
import os

import pytest

from foodcore.sources.usda import (
    Dataset,
    ReferenceStore,
    load_fdc_csv,
    normalise_unit,
    parse_portion_text,
    singularise,
    unit_kind,
)

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


def test_loads_foods_and_portions(dataset):
    assert len(dataset.foods) >= 30
    assert dataset.source == "usda"  # provenance.SOURCES member, not an edition
    assert dataset.license == "CC0-1.0"
    assert dataset.version == FIXTURE_VERSION
    assert dataset.retrieved == FIXTURE_RETRIEVED
    assert dataset.food(FLOUR).description.startswith("Wheat flour, white, all-purpose")


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


# --- schema trap 2: `amount` is not always 1 --------------------------------

def test_grams_per_unit_divides_by_amount(dataset):
    """'3 cloves = 9 g' is 3 g per clove, not 9."""
    garlic = dataset.food(GARLIC)
    rows = [p for p in garlic.portions if p.unit == "clove"]
    assert {round(p.grams_per_unit, 3) for p in rows} == {3.0}
    assert any(p.amount == 3 and p.gram_weight == 9 for p in rows)


# --- portion selection -------------------------------------------------------

def test_portion_prefers_the_unqualified_row(dataset):
    """Onions publish 'cup, chopped' 160 g and 'cup, sliced' 115 g; neither is
    the plain cup, so a bare 'cup' must not silently pick one."""
    onion = dataset.food(ONION)
    assert onion.portion("cup") is None
    flour = dataset.food(FLOUR)
    assert flour.portion("cup").grams_per_unit == 125.0


def test_size_words_are_first_class(dataset):
    onion = dataset.food(ONION)
    assert onion.portion("piece", "small").grams_per_unit == 70.0
    assert onion.portion("piece", "medium").grams_per_unit == 110.0
    assert onion.portion("piece", "large").grams_per_unit == 150.0


def test_portion_selection_is_stable_across_loads():
    a = load_fdc_csv(FIXTURE_DIR)
    b = load_fdc_csv(FIXTURE_DIR)
    for rec in a.foods:
        other = b.food(rec.fdc_id)
        for unit in ("cup", "tbsp", "piece", "slice"):
            pa, pb = rec.portion(unit), other.portion(unit)
            assert (pa.portion_id if pa else None) == (pb.portion_id if pb else None)


def test_nutrients_are_loaded_but_not_interpreted(dataset):
    """Nutrition arithmetic belongs to a sibling module; the adapter only
    carries the per-100 g rows through."""
    flour = dataset.food(FLOUR)
    assert flour.nutrients, "expected per-100g nutrient rows on the fixture"
    amount, unit = next(iter(flour.nutrients.values()))
    assert isinstance(amount, float) and isinstance(unit, str)


# --- name resolution: raw vs cooked must not merge (spec FR-01) -------------

def test_raw_and_cooked_rice_are_different_foods(dataset):
    raw, cooked = dataset.food(RICE_RAW), dataset.food(RICE_COOKED)
    assert raw.fdc_id != cooked.fdc_id
    assert raw.portion("cup").grams_per_unit == 185.0
    assert cooked.portion("cup").grams_per_unit == 158.0
    assert "raw" in raw.states and "cooked" in cooked.states


def test_bare_rice_refuses_to_pick_raw_or_cooked(dataset):
    match = dataset.resolve_food("rice")
    assert not match.confident
    assert match.food is None
    ids = {rec.fdc_id for rec, _ in match.candidates}
    assert {RICE_RAW, RICE_COOKED} <= ids
    # The refusal must carry what settles it for a human.
    assert "raw" in match.describe() and "cooked" in match.describe()


@pytest.mark.parametrize(("query", "fdc_id"), [
    ("cooked rice", RICE_COOKED),
    ("raw rice", RICE_RAW),
])
def test_naming_the_state_resolves_it(dataset, query, fdc_id):
    match = dataset.resolve_food(query)
    assert match.confident and match.food.fdc_id == fdc_id


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


def test_unique_full_token_coverage_resolves(dataset):
    assert dataset.resolve_food("egg white").food.fdc_id == EGG_WHITE
    assert dataset.resolve_food("egg whites").food.fdc_id == EGG_WHITE


def test_partial_coverage_is_not_a_match(dataset):
    assert dataset.resolve_food("unicorn tenderloin").candidates == ()


def test_sugar_without_a_qualifier_is_ambiguous(dataset):
    assert not dataset.resolve_food("sugar").confident
    assert dataset.resolve_food("brown sugar").confident


def test_resolving_by_fdc_id_is_always_confident(dataset):
    match = dataset.resolve_food(TOMATO_CAN)
    assert match.confident and match.food.fdc_id == TOMATO_CAN


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


# --- idempotent import (spec §14) -------------------------------------------

def test_reimporting_the_same_version_inserts_nothing(dataset):
    store = ReferenceStore()
    first = store.ingest(dataset)
    assert first.foods_inserted == len(dataset.foods)
    assert first.portions_inserted > 0
    foods_after_first = len(store.foods)
    portions_after_first = len(store.portions)

    second = store.ingest(dataset)
    assert second.foods_inserted == 0
    assert second.foods_updated == 0
    assert second.portions_inserted == 0
    assert second.foods_unchanged == len(dataset.foods)
    assert len(store.foods) == foods_after_first
    assert len(store.portions) == portions_after_first


def test_a_reloaded_dataset_is_still_idempotent():
    """Idempotency must key on the data, not on object identity."""
    store = ReferenceStore()
    store.ingest(load_fdc_csv(FIXTURE_DIR, version="v1", retrieved="2026-01-01"))
    n = len(store.foods)
    again = store.ingest(load_fdc_csv(FIXTURE_DIR, version="v1",
                                      retrieved="2026-01-01"))
    assert again.foods_inserted == 0 and len(store.foods) == n


def test_a_new_dataset_version_lands_alongside_the_old_one():
    store = ReferenceStore()
    store.ingest(load_fdc_csv(FIXTURE_DIR, version="v1", retrieved="2026-01-01"))
    n = len(store.foods)
    store.ingest(load_fdc_csv(FIXTURE_DIR, version="v2", retrieved="2026-06-01"))
    assert len(store.foods) == 2 * n
    versions = {k[1] for k in store.foods}
    assert versions == {"v1", "v2"}


def test_a_changed_record_updates_in_place_rather_than_duplicating(dataset):
    store = ReferenceStore()
    store.ingest(dataset)
    n = len(store.foods)
    edited = dataset.food(FLOUR)
    mutated = Dataset(
        version=dataset.version,
        retrieved=dataset.retrieved,
        foods=(type(edited)(
            fdc_id=edited.fdc_id,
            description="Wheat flour, white, all-purpose, RENAMED",
            data_type=edited.data_type,
            category_id=edited.category_id,
            publication_date=edited.publication_date,
            portions=edited.portions,
            nutrients=edited.nutrients,
        ),),
    )
    stats = store.ingest(mutated)
    assert stats.foods_updated == 1 and stats.foods_inserted == 0
    assert len(store.foods) == n


def test_reference_data_is_not_group_scoped():
    """A tenant key in reference data would be a modelling error: a gram of
    flour is the same gram in every household."""
    keys = ReferenceStore().foods
    assert keys == {}
    store = ReferenceStore()
    store.ingest(load_fdc_csv(FIXTURE_DIR, version="v1", retrieved="2026-01-01"))
    assert all(len(k) == 3 for k in store.foods)   # (source, version, fdc_id)
