"""The conversion engine, including the user's real cases.

Expected grams come from the real USDA portion rows in the fixture, so where a
number here disagrees with folklore ("a cup of flour is 120 g") the dataset
wins — that is the entire point of importing it.
"""
import pytest

from foodcore.convert import (
    CONF_COUNT_SIZED,
    CONF_COUNT_UNSIZED,
    CONF_PHYSICAL,
    PackageSize,
    convert,
    convert_text,
    parse_quantity,
)
from foodcore.provenance import Provenance, Resolved, Unresolved

FLOUR = "168894"
ONION = "170000"
RICE_RAW = "168877"
RICE_COOKED = "168878"
EGG_WHITE = "172183"
ORANGE = "169097"
TOMATO_CAN = "170051"
HONEY = "169640"
MILK = "171265"
GARLIC = "169230"
OIL = "171413"
BREAD = "174924"
CELERY = "169988"
SPINACH = "168462"


def rec(dataset, fdc_id):
    """The full record for an FDC id, the way a host app asks for one.

    `Dataset.food(key)` is the lightweight candidate projection; the engines
    take a `FoodRecord`, which is what `food_record(key)` returns.
    """
    return dataset.food_record(f"usda:{fdc_id}")


def approx(value, expected, tol=0.01):
    assert value == pytest.approx(expected, rel=tol), f"{value} != {expected}"


# =========================================================================
# (a) exact physical conversion — method=measured, confidence 1.0
# =========================================================================

@pytest.mark.parametrize(("amount", "src", "dst", "expected"), [
    (1, "tsp", "ml", 4.92892),
    (2, "tbsp", "ml", 29.5736),
    (1, "cup", "ml", 236.588),
    (1, "lb", "g", 453.59237),
    (1.5, "pounds", "g", 680.388555),
    (16, "oz", "lb", 1.0),
    (1000, "g", "kg", 1.0),
    (1, "gallon", "l", 3.78541),
])
def test_physical_conversions_need_no_food(amount, src, dst, expected):
    got = convert(amount, src, dst)
    assert isinstance(got, Resolved)
    approx(got.value, expected)
    assert got.unit == dst
    assert got.provenance.method == "measured"
    assert got.provenance.confidence == CONF_PHYSICAL


def test_physical_provenance_is_definitional_not_a_dataset_reading():
    prov = convert(1, "lb", "g").provenance
    assert prov.source == "internal"   # closed vocabulary; the citation...
    assert prov.source_id.startswith("intl-yard-and-pound-1959:")  # ...lives here
    assert prov.retrieved == "1959-07-01"       # intl. yard-and-pound agreement
    assert prov.license == "internal"


def test_user_case_240_g_egg_whites(dataset):
    """'240 g egg whites' is already a weight: identity, measured, 1.0."""
    got = convert_text("240 g egg whites", "g", dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 240.0)
    assert got.provenance.method == "measured"
    assert got.provenance.confidence == 1.0


# =========================================================================
# (b) ingredient-dependent volume -> mass — method=calculated
# =========================================================================

def test_user_case_2_cups_flour(dataset):
    """USDA 168894: 1 cup = 125 g, so two cups is 250 g. (Folk wisdom says
    120-140 g/cup; the measured record is what we ship.)"""
    got = convert_text("2 cups flour", "g", dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 250.0)
    assert got.unit == "g"
    assert got.provenance.method == "calculated"
    assert got.provenance.source == "usda"
    assert got.provenance.source_id.startswith(FLOUR + "#")
    assert got.provenance.license == "CC0-1.0"
    assert got.provenance.confidence == 0.9


def test_user_case_2_tablespoons_honey(dataset):
    """USDA 169640: 1 tbsp honey = 21 g."""
    got = convert_text("2 tablespoons honey", "g", dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 42.0)
    assert got.provenance.method == "calculated"


def test_the_same_volume_of_different_foods_differs(dataset):
    """The reason (b) cannot be folded into (a): a cup is not a weight."""
    flour = convert_text("1 cup flour", "g", dataset=dataset).value
    honey = convert_text("1 cup honey", "g", dataset=dataset).value
    oil = convert_text("1 cup olive oil", "g", dataset=dataset).value
    approx(flour, 125.0)
    approx(honey, 339.0)
    approx(oil, 216.0)


def test_volume_unit_without_its_own_portion_row_derives_a_density(dataset):
    """Milk publishes cup/tbsp/fl oz/quart but not litres. 244 g per 236.588 ml
    is 1.0313 g/ml, so 2 l is ~2062 g — derived, and flagged as less certain
    than a direct portion row."""
    got = convert(2, "l", "g", food=rec(dataset, MILK), dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 2062.6, tol=0.005)
    assert got.provenance.method == "calculated"
    assert got.provenance.confidence == 0.8


def test_mass_to_volume_uses_the_same_density(dataset):
    got = convert(244, "g", "cup", food=rec(dataset, MILK), dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 1.0)
    assert got.provenance.method == "calculated"


@pytest.mark.parametrize(("phrase", "expect_in_detail"), [
    # 'cup, chopped' 160 g vs 'cup, sliced' 115 g — a 39% spread
    ("1 cup onions", ("chopped", "sliced")),
    # 'cup packed' 220 g vs 'cup unpacked' 145 g — a 52% spread
    ("1 cup brown sugar", ("packed", "unpacked")),
])
def test_a_bare_cup_is_refused_when_only_prepared_cups_are_published(
    dataset, phrase, expect_in_detail
):
    """The dataset has rows that would answer this, and they disagree with each
    other by up to 2x. Choosing one silently is the fabrication being refused."""
    got = convert_text(phrase, "g", dataset=dataset)
    assert isinstance(got, Unresolved)
    assert got.reason == "qualifier_required"
    for word in expect_in_detail:
        assert word in got.detail


def test_an_unqualified_volume_row_rescues_a_food_with_only_prepared_cups(dataset):
    """Cream publishes only 'cup, whipped' (120 g) and 'cup, fluid' (238 g) —
    but it also publishes a plain 'fl oz' (29.8 g), which IS unambiguous
    evidence of density. So '1 cup cream' resolves via the derived-density path
    at the lower confidence, rather than refusing. Refusal is for when there is
    no unambiguous row at all."""
    got = convert_text("1 cup cream", "g", dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 238.5, tol=0.02)
    assert got.provenance.confidence == 0.8


def test_volume_to_mass_without_a_food_is_refused():
    got = convert(1, "cup", "g")
    assert isinstance(got, Unresolved)
    assert got.reason == "food_required"
    assert "ingredient" in got.detail


# =========================================================================
# (c) product / package specific — the headline refusal
# =========================================================================

def test_user_case_1_can_tomatoes_is_unresolved(dataset):
    """Spec FR-02's acceptance criterion. The dataset DOES publish a 'can'
    portion for this food (190 g drained), and using it would be the plausible
    wrong answer — a UK tin is 400 g."""
    got = convert_text("1 can tomatoes", "g", dataset=dataset)
    assert isinstance(got, Unresolved)
    assert got.reason == "no_package_size"
    assert "package" in got.detail


def test_the_dataset_really_does_contain_a_tempting_can_row(dataset):
    """Mutation canary: if someone 'fixes' the refusal by using this row, the
    test above goes red — and this one documents what they would have used."""
    can_rows = [p for p in rec(dataset, TOMATO_CAN).portions if p.measure == "can"]
    assert can_rows and can_rows[0].gram_weight == 190.0


@pytest.mark.parametrize("unit", ["can", "tin", "jar", "bottle", "package",
                                  "packet", "box", "container", "carton"])
def test_every_package_unit_refuses_without_a_declared_size(dataset, unit):
    got = convert(1, unit, "g", food=rec(dataset, TOMATO_CAN), dataset=dataset)
    assert isinstance(got, Unresolved)
    assert got.reason == "no_package_size"


def test_a_declared_package_size_resolves_as_declared(dataset):
    pkg = PackageSize(net_g=400.0, label="Mutti polpa 400g",
                      source_id="ean:8005110012345", retrieved="2026-10-01")
    got = convert(2, "can", "g", food=rec(dataset, TOMATO_CAN), dataset=dataset,
                  package=pkg)
    assert isinstance(got, Resolved)
    approx(got.value, 800.0)
    assert got.provenance.method == "declared"
    assert got.provenance.source == "internal"  # caller-declared, not a dataset
    assert got.provenance.version == "convert/1"
    assert got.provenance.source_id == "ean:8005110012345"
    assert got.provenance.confidence == 0.95


def test_a_declared_package_in_ml_is_weighed_through_the_food(dataset):
    """A 1-litre carton of milk weighs more than a kilo."""
    pkg = PackageSize(net_ml=1000.0, label="1 l carton", source_id="pkg:milk-1l")
    got = convert(1, "carton", "g", food=rec(dataset, MILK), dataset=dataset,
                  package=pkg)
    assert isinstance(got, Resolved)
    approx(got.value, 1031.3, tol=0.005)


def test_mass_to_package_count(dataset):
    pkg = PackageSize(net_g=400.0, source_id="pkg:tin-400")
    got = convert(1.2, "kg", "can", food=rec(dataset, TOMATO_CAN),
                  dataset=dataset, package=pkg)
    assert isinstance(got, Resolved)
    approx(got.value, 3.0)


def test_package_refusal_survives_an_empty_package(dataset):
    got = convert(1, "can", "g", food=rec(dataset, TOMATO_CAN), dataset=dataset,
                  package=PackageSize())
    assert isinstance(got, Unresolved)
    assert got.reason == "no_package_size"


# =========================================================================
# (d) estimated count -> mass — method=estimated, lower confidence
# =========================================================================

def test_user_case_3_medium_onions(dataset):
    """USDA 170000: 1 medium onion = 110 g."""
    got = convert_text("3 medium onions", "g", dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 330.0)
    assert got.provenance.method == "estimated"
    assert got.provenance.confidence == CONF_COUNT_SIZED
    assert got.provenance.confidence < CONF_PHYSICAL


@pytest.mark.parametrize(("phrase", "expected"), [
    ("3 small onions", 210.0),
    ("3 medium onions", 330.0),
    ("3 large onions", 450.0),
])
def test_size_words_change_the_answer(dataset, phrase, expected):
    approx(convert_text(phrase, "g", dataset=dataset).value, expected)


def test_count_units_are_first_class(dataset):
    approx(convert_text("4 cloves garlic", "g", dataset=dataset).value, 12.0)
    approx(convert_text("2 slices bread", "g", dataset=dataset).value, 58.0)
    approx(convert_text("1 bunch spinach", "g", dataset=dataset).value, 340.0)
    approx(convert_text("2 medium stalks celery", "g", dataset=dataset).value, 80.0)


def test_a_sizeless_count_is_resolved_at_lower_confidence(dataset):
    """'1 onion' — the dataset only knows small/medium/large, so standing
    'medium' in is a convention and must be reported as the weakest estimate."""
    got = convert(1, "piece", "g", food=rec(dataset, ONION), dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 110.0)
    assert got.provenance.method == "estimated"
    assert got.provenance.confidence == CONF_COUNT_UNSIZED
    assert "size=medium" in got.provenance.source_id   # the assumption is visible


def test_a_size_the_dataset_does_not_publish_is_refused(dataset):
    got = convert(1, "piece", "g", food=rec(dataset, ONION), dataset=dataset,
                  size="jumbo")
    assert isinstance(got, Unresolved)
    assert got.reason == "no_portion_record"
    assert "large" in got.detail        # tells the caller what IS available


def test_user_case_1_5_pounds_oranges_gives_an_approximate_count(dataset):
    """680.4 g at 131 g per orange is about 5 oranges. Not rounded here — the
    caller decides whether that reads as '5' or '5-6' — but it is flagged as
    the weakest method in the module."""
    got = convert_text("1.5 pounds oranges", "piece", dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 5.194, tol=0.01)
    assert round(got.value) == 5
    assert got.unit == "piece"
    assert got.provenance.method == "estimated"
    assert got.provenance.confidence <= 0.4


def test_mass_to_count_for_egg_whites(dataset):
    """240 g of egg white is about 7 large whites (33 g each)."""
    got = convert(240, "g", "piece", food=rec(dataset, EGG_WHITE),
                  dataset=dataset, size="large")
    assert isinstance(got, Resolved)
    approx(got.value, 7.27, tol=0.01)
    assert got.provenance.method == "estimated"


def test_count_to_count_in_the_same_unit_is_measured(dataset):
    got = convert(3, "clove", "clove", food=rec(dataset, GARLIC), dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 3.0)
    assert got.provenance.method == "measured"


def test_a_count_with_no_portion_record_is_refused(dataset):
    got = convert(1, "fillet", "g", food=rec(dataset, FLOUR), dataset=dataset)
    assert isinstance(got, Unresolved)
    assert got.reason == "no_portion_record"


# =========================================================================
# The four kinds are distinguishable from the outside
# =========================================================================

def test_each_kind_reports_its_own_method(dataset):
    kinds = {
        "physical": convert(1, "lb", "g"),
        "ingredient": convert_text("1 cup flour", "g", dataset=dataset),
        "package": convert(1, "can", "g", food=rec(dataset, TOMATO_CAN),
                           dataset=dataset,
                           package=PackageSize(net_g=400.0, source_id="x")),
        "count": convert_text("1 medium onion", "g", dataset=dataset),
    }
    assert kinds["physical"].provenance.method == "measured"
    assert kinds["ingredient"].provenance.method == "calculated"
    assert kinds["package"].provenance.method == "declared"
    assert kinds["count"].provenance.method == "estimated"
    confidences = [kinds[k].provenance.confidence
                   for k in ("physical", "package", "ingredient", "count")]
    assert confidences == sorted(confidences, reverse=True), (
        "confidence must fall as the evidence weakens"
    )


def test_every_resolution_carries_full_provenance(dataset):
    got = convert_text("2 cups flour", "g", dataset=dataset)
    p = got.provenance
    assert isinstance(p, Provenance)
    assert all([p.source, p.source_id, p.retrieved, p.method, p.version,
                p.license])
    assert 0.0 < p.confidence <= 1.0


def test_a_two_step_conversion_is_only_as_good_as_its_weaker_half(dataset):
    """mass -> count chains a measured step onto an estimated one; the result
    must not advertise the measured half's confidence."""
    got = convert_text("1.5 pounds oranges", "piece", dataset=dataset)
    assert got.provenance.method == "estimated"
    assert got.provenance.confidence < CONF_PHYSICAL


# =========================================================================
# Raw vs cooked must not merge (spec FR-01)
# =========================================================================

def test_one_cup_of_rice_is_unresolved_because_raw_and_cooked_differ(dataset):
    got = convert_text("1 cup rice", "g", dataset=dataset)
    assert isinstance(got, Unresolved)
    assert got.reason == "ambiguous_food"
    assert "raw" in got.detail and "cooked" in got.detail


def test_raw_and_cooked_rice_convert_to_different_weights(dataset):
    raw = convert_text("1 cup raw rice", "g", dataset=dataset)
    cooked = convert_text("1 cup cooked rice", "g", dataset=dataset)
    approx(raw.value, 185.0)
    approx(cooked.value, 158.0)
    assert raw.value != cooked.value
    assert raw.provenance.source_id.startswith(RICE_RAW + "#")
    assert cooked.provenance.source_id.startswith(RICE_COOKED + "#")


def test_dry_and_cooked_oats_also_differ(dataset):
    dry = convert_text("1 cup dry oats", "g", dataset=dataset)
    cooked = convert_text("1 cup cooked oats", "g", dataset=dataset)
    approx(dry.value, 81.0)
    approx(cooked.value, 234.0)


# =========================================================================
# Refusals, enumerated
# =========================================================================

@pytest.mark.parametrize(("call", "reason"), [
    (lambda d: convert(0, "cup", "ml"), "nonpositive_amount"),
    (lambda d: convert(-1, "cup", "ml"), "nonpositive_amount"),
    (lambda d: convert(1, "furlong", "g"), "unknown_unit"),
    (lambda d: convert(1, "cup", "parsec"), "unknown_unit"),
    (lambda d: convert(1, "cup", "g"), "food_required"),
    (lambda d: convert_text("1 can tomatoes", "g", dataset=d), "no_package_size"),
    (lambda d: convert_text("1 cup rice", "g", dataset=d), "ambiguous_food"),
    (lambda d: convert_text("2 cups unobtainium", "g", dataset=d), "unknown_food"),
    (lambda d: convert_text("flour", "g", dataset=d), "no_quantity"),
    (lambda d: convert_text("1 cup onions", "g", dataset=d), "qualifier_required"),
    (lambda d: convert(1, "piece", "g", food=rec(d, ONION), dataset=d,
                       size="jumbo"), "no_portion_record"),
])
def test_refusal_reasons(dataset, call, reason):
    got = call(dataset)
    assert isinstance(got, Unresolved), f"expected a refusal, got {got}"
    assert got.reason == reason
    assert got.detail, "a refusal must say what was missing"


def test_a_refusal_never_carries_a_number(dataset):
    got = convert_text("1 can tomatoes", "g", dataset=dataset)
    assert not hasattr(got, "value")


# =========================================================================
# Free-text parsing
# =========================================================================

@pytest.mark.parametrize(("text", "amount", "unit", "size", "food"), [
    ("2 cups flour", 2.0, "cup", None, "flour"),
    ("1 1/2 cups milk", 1.5, "cup", None, "milk"),
    ("1/2 cup sugar", 0.5, "cup", None, "sugar"),
    ("1½ cups milk", 1.5, "cup", None, "milk"),
    ("½ cup milk", 0.5, "cup", None, "milk"),
    ("3 medium onions", 3.0, "piece", "medium", "onions"),
    ("3 onions", 3.0, None, None, "onions"),
    ("1 can tomatoes", 1.0, "can", None, "tomatoes"),
    ("1.5 pounds oranges", 1.5, "lb", None, "oranges"),
    ("240 g egg whites", 240.0, "g", None, "egg whites"),
    ("4 cloves garlic", 4.0, "clove", None, "garlic"),
    ("2 fl oz lemon juice", 2.0, "fl oz", None, "lemon juice"),
    ("about 2 cups milk", 2.0, "cup", None, "milk"),
    ("1 slice, large bread", 1.0, "slice", "large", "bread"),
])
def test_parse_quantity(text, amount, unit, size, food):
    p = parse_quantity(text)
    assert (p.amount, p.unit, p.size) == (amount, unit, size)
    assert p.food_text == food


def test_a_bare_count_assumes_a_whole_piece(dataset):
    """'3 onions' has no unit word; the unit is 'one whole onion'."""
    got = convert_text("3 onions", "g", dataset=dataset)
    assert isinstance(got, Resolved)
    approx(got.value, 330.0)
    assert got.provenance.confidence == CONF_COUNT_UNSIZED


# --- the food only matters when the units cross dimensions ------------------

def test_a_mass_to_mass_conversion_does_not_need_to_identify_the_food(dataset):
    """REGRESSION, found by running a REAL USDA import rather than the fixture.

    Pounds to grams is the 1959 yard-and-pound agreement — true of flour and of
    feathers. Resolving the food first made '2 lb flour' refuse as
    `ambiguous_food`, because against 7,793 real foods "flour" legitimately
    matches arrowroot, carob, millet and potato flour. That is a REGRESSION
    against a plain unit table, and it is invisible against a 37-food fixture
    where nothing collides — which is exactly why it shipped.
    """
    for text, expected in (("2 lb flour", 907.18474),
                           ("240 g egg whites", 240.0),
                           ("1 kg rice", 1000.0)):
        got = convert_text(text, "g", dataset=dataset)
        assert getattr(got, "resolved", False), f"{text}: {got.reason} {got.detail}"
        assert got.value == pytest.approx(expected, rel=1e-6)
        # And it is definitional, not a lookup.
        assert got.provenance.method == "measured"


def test_like_unit_conversions_work_with_no_dataset_at_all():
    """They are physics; a host app with no reference data imported must still
    get them. Previously this raised AttributeError on `dataset.resolve_food`,
    which also broke the module's never-raises contract."""
    got = convert_text("2 lb flour", "g")

    assert getattr(got, "resolved", False)
    assert got.value == pytest.approx(907.18474, rel=1e-6)


def test_a_cross_dimension_conversion_without_a_dataset_refuses_cleanly():
    """Volume to mass needs a density, which needs the food. With no dataset
    that is an honest Unresolved — not an exception."""
    got = convert_text("2 cups flour", "g")

    assert not getattr(got, "resolved", False)
    assert got.reason == "no_dataset"
