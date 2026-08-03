"""Measurement parse / scale / weight-conversion (foundation for the serving
scaler, planner, and shopping consolidation)."""
import pytest

from app.services import units


@pytest.mark.parametrize("line,qty,unit,rest", [
    ("2 cups flour", 2.0, "cup", "flour"),
    ("1 1/2 tbsp olive oil", 1.5, "tbsp", "olive oil"),
    ("½ tsp salt", 0.5, "tsp", "salt"),
    ("200 g sugar", 200.0, "g", "sugar"),
    ("1 lb ground beef", 1.0, "lb", "ground beef"),
    # Range still takes the low end. "clove" IS now a unit — it used to be left
    # in the food name, so "2 cloves garlic" scaled as a bare number with the
    # measure stuck to the ingredient. It converts to no weight (see
    # test_count_units_never_claim_a_weight_or_volume), which is the honest
    # answer for a clove.
    ("2-3 cloves garlic", 2.0, "clove", "garlic"),
    ("salt to taste", None, None, "salt to taste"),
    ("3 eggs", 3.0, None, "eggs"),
])
def test_parse_line(line, qty, unit, rest):
    p = units.parse_line(line)
    assert p["qty"] == qty
    assert p["unit"] == unit
    assert p["rest"] == rest


@pytest.mark.parametrize("line,factor,expected", [
    ("2 cups flour", 2, "4 cup flour"),
    ("1/2 tsp salt", 3, "1 1/2 tsp salt"),
    ("200 g sugar", 0.5, "100 g sugar"),
    ("salt to taste", 4, "salt to taste"),   # no quantity → unchanged
    ("3 eggs", 2, "6 eggs"),
])
def test_scale_line(line, factor, expected):
    assert units.scale_line(line, factor) == expected


def test_to_grams_weight_and_volume_with_density():
    assert units.to_grams("200 g sugar") == pytest.approx(200)
    assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53, rel=0.01)
    assert units.to_grams("2 tbsp olive oil") == pytest.approx(14.7868 * 2 * 0.92, rel=0.01)


def test_to_grams_none_without_unit_or_density():
    assert units.to_grams("3 eggs") is None            # no unit
    assert units.to_grams("2 cups diced onion") is None  # no known density


def test_to_weight_line_converts_when_possible():
    assert units.to_weight_line("1 cup flour").endswith("g flour")
    assert units.to_weight_line("2 eggs") == "2 eggs"  # unchanged


def test_annotate_weight_appends_parenthetical_keeping_original():
    out = units.annotate_weight("1 cup flour")
    assert out.startswith("1 cup flour")      # original measure kept
    assert out.endswith(")") and "g)" in out  # weight appended in parens


def test_annotate_weight_unchanged_when_not_convertible():
    assert units.annotate_weight("3 eggs") == "3 eggs"
    assert units.annotate_weight("2 cups diced onion") == "2 cups diced onion"


# --- Scraped-recipe ingredient lines ---------------------------------------
#
# Every case below is a shape that real recipe sites emit and that the parser
# previously left without a unit — or, for the approximator case, without a
# quantity at all. An unparsed line still DISPLAYS fine, which is why this went
# unnoticed; the cost is that it scales wrong, converts wrong, and consolidates
# badly on a shopping list.

@pytest.mark.parametrize("line,qty,unit,rest", [
    # -- previously broken --
    ("2 c. sugar", 2.0, "cup", "sugar"),                       # trailing period
    ("2 tbsp. olive oil", 2.0, "tbsp", "olive oil"),
    ("About 2 cups milk", 2.0, "cup", "milk"),                 # leading approximator
    ("Approximately 1 tsp salt", 1.0, "tsp", "salt"),
    ("a scant 1/2 cup cream", 0.5, "cup", "cream"),
    ("1 pinch of nutmeg", 1.0, "pinch", "of nutmeg"),          # informal unit
    ("2 cloves garlic, minced", 2.0, "clove", "garlic, minced"),
    ("1 (14.5 oz) can diced tomatoes", 1.0, "can", "(14.5 oz) diced tomatoes"),
    # -- already worked; pinned so the changes above don't regress them --
    ("1 1/2 cups all-purpose flour", 1.5, "cup", "all-purpose flour"),
    ("½ cup butter, softened", 0.5, "cup", "butter, softened"),
    ("250g plain flour", 250.0, "g", "plain flour"),
    ("2 lbs chicken thighs", 2.0, "lb", "chicken thighs"),
    ("1 to 2 teaspoons salt", 1.0, "tsp", "salt"),             # range: low end
    ("3 large eggs", 3.0, None, "large eggs"),                 # no unit is correct
    ("Salt and pepper to taste", None, None, "Salt and pepper to taste"),
])
def test_parse_line_handles_real_scraped_shapes(line, qty, unit, rest):
    got = units.parse_line(line)

    assert got["qty"] == qty
    assert got["unit"] == unit
    assert got["rest"] == rest


@pytest.mark.parametrize("unit", [
    "pinch", "dash", "handful", "clove", "slice", "can", "package", "stick",
    "sprig", "bunch", "head",
])
def test_count_units_never_claim_a_weight_or_volume(unit):
    """They are units for scaling only. Giving "1 clove" a gram value would
    make every weight readout quietly wrong, which is worse than declining."""
    assert units.canonical_unit(unit) == unit     # recognised as a unit...
    assert units.dimension(unit) is None          # ...but converts to nothing


def test_a_count_unit_scales_without_converting():
    assert units.to_grams("2 cloves garlic") is None
    assert units.scale_line("2 cloves garlic", 2).startswith("4 ")


def test_parsing_never_rewrites_the_humans_line():
    """display stays the source of truth; the parse is derived from it. A
    stripped approximator must not disappear from what the cook reads."""
    line = "About 2 cups milk"

    assert units.parse_line(line)["rest"] == "milk"
    assert units.scale_line(line, 1) is not None   # never raises on the original
