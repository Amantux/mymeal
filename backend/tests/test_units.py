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
    # Word units agree with the SCALED quantity, not the original: this used to
    # read "4 cup flour" for every normally-written recipe.
    ("2 cups flour", 2, "4 cups flour"),
    ("1 cup flour", 2, "2 cups flour"),
    ("2 cups flour", 0.5, "1 cup flour"),       # back down to the singular
    ("1/2 tsp salt", 3, "1 1/2 tsp salt"),      # abbreviation: never pluralized
    ("3 tablespoons oil", 2, "6 tbsp oil"),     # canonicalizes TO an abbreviation
    ("200 g sugar", 0.5, "100 g sugar"),
    ("2 cloves garlic", 2, "4 cloves garlic"),
    ("1 clove garlic", 3, "3 cloves garlic"),
    ("2 pinches salt", 0.5, "1 pinch salt"),    # -es plural, singularized
    ("salt to taste", 4, "salt to taste"),      # no quantity → unchanged
    ("3 eggs", 2, "6 eggs"),                    # no unit → nothing to pluralize
])
def test_scale_line(line, factor, expected):
    assert units.scale_line(line, factor) == expected


@pytest.mark.parametrize("unit,qty,expected", [
    ("cup", 1, "cup"),
    ("cup", 2, "cups"),
    # Recipe English: fractions under 1 keep the singular ("1/2 cup flour"),
    # and so does 1.5's whole part being 1 — only above 1 goes plural.
    ("cup", 0.5, "cup"),
    ("cup", 0.75, "cup"),
    ("cup", 1.5, "cups"),
    ("clove", 4, "cloves"),
    ("bunch", 2, "bunches"),
    ("dash", 3, "dashes"),
    ("tsp", 4, "tsp"),          # abbreviations stay put
    ("g", 200, "g"),
    ("fl oz", 8, "fl oz"),
    ("", 2, ""),
    (None, 2, ""),
    ("cup", None, "cup"),       # unknown quantity → don't guess
])
def test_pluralize_unit(unit, qty, expected):
    assert units.pluralize_unit(unit, qty) == expected


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


# A pasted blog / YouTube ingredient list is bulleted far more often than not,
# and the quantity match is anchored at the start of the line — so every one of
# these used to parse to nothing at all: no quantity, no unit, and the marker
# itself dumped into the food name.

@pytest.mark.parametrize("line", [
    "- 2 cups flour",
    "– 2 cups flour",       # en dash
    "— 2 cups flour",       # em dash
    "* 2 cups flour",
    "• 2 cups flour",
    "▢ 2 cups flour",       # the checkbox glyph several recipe plugins emit
    "‣ 2 cups flour",
    "◦ 2 cups flour",
    "1. 2 cups flour",      # numbered marker
    "2) 2 cups flour",
    "•2 cups flour",        # bullet hugging the text
])
def test_list_markers_are_stripped_before_parsing(line):
    got = units.parse_line(line)

    assert got["qty"] == 2.0
    assert got["unit"] == "cup"
    assert got["rest"] == "flour"


@pytest.mark.parametrize("line,qty,unit,rest", [
    # A decimal must never be mistaken for a numbered list marker: the numbered
    # form requires trailing whitespace precisely so this keeps working.
    ("1.5 cups flour", 1.5, "cup", "flour"),
    ("2 cups flour", 2.0, "cup", "flour"),
    # A hyphenated food name is not a bullet either.
    ("all-purpose flour", None, None, "all-purpose flour"),
])
def test_marker_stripping_does_not_eat_real_content(line, qty, unit, rest):
    got = units.parse_line(line)

    assert (got["qty"], got["unit"], got["rest"]) == (qty, unit, rest)


def test_leading_bracket_no_longer_blocks_the_quantity():
    """"(optional) 1 tbsp x" used to parse to nothing because the quantity match
    is anchored. The bracket stays in the food text, where a cook reads it."""
    got = units.parse_line("(optional) 1 tbsp chili flakes")

    assert got["qty"] == 1.0
    assert got["unit"] == "tbsp"
    assert got["rest"] == "(optional) chili flakes"


def test_a_bracket_with_no_quantity_after_it_is_left_alone():
    """No silent rewriting when looking past the bracket doesn't actually help."""
    line = "(about 2 cups) chopped kale"

    assert units.parse_line(line) == {
        "qty": None, "unit": None, "rest": line, "range_hi": None,
    }


@pytest.mark.parametrize("line,hi", [
    ("2-3 cloves garlic", 3.0),
    ("2 to 3 tbsp olive oil", 3.0),
    ("1–2 cups stock", 2.0),        # en dash
    ("2 cups flour", None),         # not a range
])
def test_parse_line_reports_the_range_high_end(line, hi):
    assert units.parse_line(line)["range_hi"] == hi


def test_parse_line_keeps_the_low_end_authoritative_for_a_range():
    """range_hi is additive: the structured quantity every other consumer reads
    (shopping consolidation, weight conversion, MCP/HA) is unchanged."""
    got = units.parse_line("2-3 cloves garlic")

    assert got["qty"] == 2.0
    assert got["unit"] == "clove"
    assert got["rest"] == "garlic"


@pytest.mark.parametrize("line,factor,expected", [
    # Both ends scale. This used to silently destroy the high end: the regex
    # consumed "-3" and never captured it, so "2-3 cloves" doubled to "4 cloves".
    ("2-3 cloves garlic", 2, "4-6 cloves garlic"),
    ("2 to 3 tbsp olive oil", 2, "4-6 tbsp olive oil"),
    ("1-2 cups stock", 0.5, "1/2-1 cup stock"),
])
def test_scale_line_keeps_both_ends_of_a_range(line, factor, expected):
    assert units.scale_line(line, factor) == expected


def test_scale_line_keeps_the_approximator():
    """A scaled amount is still approximate. Dropping "About" made the new line
    read more precise than the recipe actually is."""
    assert units.scale_line("About 2 cups milk", 2) == "About 4 cups milk"


def test_scale_line_drops_a_list_marker_rather_than_stranding_it():
    assert units.scale_line("- 2 cups flour", 2) == "4 cups flour"
