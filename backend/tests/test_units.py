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
    assert units.parse_line(line)["prefix"] == "About"   # reported, not dropped
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
    is anchored. The bracket is reported as a PREFIX, not folded into the food:
    a caller rewriting the line has to put it back in front, or scaling emits
    "2 tbsp (optional) chili flakes" — words in an order nobody wrote."""
    got = units.parse_line("(optional) 1 tbsp chili flakes")

    assert got["qty"] == 1.0
    assert got["unit"] == "tbsp"
    assert got["rest"] == "chili flakes"
    assert got["prefix"] == "(optional)"


def test_a_bracket_with_no_quantity_after_it_is_left_alone():
    """No silent rewriting when looking past the bracket doesn't actually help."""
    line = "(about 2 cups) chopped kale"

    assert units.parse_line(line) == {
        "qty": None, "unit": None, "rest": line, "range_hi": None, "prefix": "",
        "alt": "", "optional": False,
    }


@pytest.mark.parametrize("line,qty,expected", [
    # THE case that silently deleted the user's number: a row can legitimately
    # store quantity=0 next to a numeric display (the AI structuring path writes
    # exactly that, and a legacy NULL looks the same). Deriving the amount from
    # `quantity` while stripping it from the text independently rendered
    # "cup | flour" — the "2" gone from the page.
    ("2 cups flour", 0, ("2", "cups", "flour")),
    ("2 cups flour", None, ("2", "cups", "flour")),
    ("2 cups flour", 2, ("2", "cups", "flour")),
    # The structured quantity wins when it has one (that's what scaling updates).
    ("1 cup milk", 1.5, ("1 1/2", "cups", "milk")),
    # No leading amount -> nothing is lifted out, so nothing can go missing.
    ("Juice of 1 lemon", 1, ("", "", "Juice of 1 lemon")),
    ("Salt to taste", 0, ("", "", "Salt to taste")),
    ("a good knob of butter", 0, ("", "", "a good knob of butter")),
    # Text in FRONT of the amount can't sit in a numeric column and can't move
    # behind it without garbling the line, so the row stays whole.
    ("About 2 cups milk", 2, ("", "", "About 2 cups milk")),
    ("(optional) 1 tbsp chili flakes", 1, ("", "", "(optional) 1 tbsp chili flakes")),
    # The weight annotation travels with the food text.
    ("1 cup flour (125 g)", 1, ("1", "cup", "flour (125 g)")),
    # A raw float never reaches the reader.
    ("2/3 cup granulated sugar", 0.6667, ("2/3", "cup", "granulated sugar")),
])
def test_split_amount_is_lossless(line, qty, expected):
    assert units.split_amount(line, qty) == expected


def test_split_amount_never_strips_without_showing():
    """The invariant: if the amount was removed from the text, it MUST appear in
    the amount column. Checked over every shape above rather than case by case."""
    for line in ["2 cups flour", "1 1/2 lbs pork", "2-3 cloves garlic", "3 eggs",
                 "Salt to taste", "About 2 cups milk", "1 cup flour (125 g)"]:
        amount, unit, rest = units.split_amount(line, 0)
        stripped = rest != line
        assert bool(amount) == stripped, f"{line!r} -> {(amount, unit, rest)!r}"


@pytest.mark.parametrize("line", [
    # A leading asterisk on an ingredient is a FOOTNOTE reference; stripping it
    # orphans the footnote. Only a bullet followed by whitespace is a bullet.
    "*2 tbsp cornstarch",
    "-1 cup cream",
])
def test_a_marker_hugging_the_text_is_not_treated_as_a_bullet(line):
    assert units.parse_line(line)["rest"] == line
    assert units.scale_line(line, 2) == line


def test_scale_line_puts_a_leading_bracket_back_in_front():
    """It used to land in the middle of the ingredient: "2 tbsp (optional) chili
    flakes" — text the user never wrote."""
    assert units.scale_line("(optional) 1 tbsp chili flakes", 2) \
        == "(optional) 2 tbsp chili flakes"


@pytest.mark.parametrize("line,factor,expected", [
    # The defect: scaling 4→6 servings printed "337 1/2 g". Nobody weighs half a
    # gram of mushrooms, and the fraction claims precision the scaling never had.
    ("225 g flour", 1.5, "338 g flour"),
    ("750 g piece beef fillet", 1.25, "938 g piece beef fillet"),
    ("500 ml stock", 1.5, "750 ml stock"),
    # Kilos and litres are big enough for a fraction to be real — as a decimal.
    ("1 kg beef", 1.19, "1.19 kg beef"),
    ("2 l water", 0.5, "1 l water"),            # exact value stays whole
    # Sub-gram amounts keep a decimal rather than rounding away to "0".
    ("2 g saffron", 0.25, "0.5 g saffron"),
    # Ranges get the same treatment at BOTH ends.
    ("200-300 g pasta", 1.5, "300-450 g pasta"),
    # Recipe English keeps fractions for everything else — unchanged.
    ("1/2 tsp salt", 3, "1 1/2 tsp salt"),
    ("1 cup flour", 1.5, "1 1/2 cups flour"),
    ("1 lb beef", 1.5, "1 1/2 lb beef"),
])
def test_scale_line_renders_metric_amounts_as_decimals_not_fractions(line, factor, expected):
    assert units.scale_line(line, factor) == expected


def test_format_qty_for_unit_only_overrides_the_metric_units():
    """The fraction rendering is right for how recipes are written; it is only
    wrong for g/ml/kg/l. Everything else must still route to format_qty."""
    assert units.format_qty_for_unit(337.5, "g") == "338"
    assert units.format_qty_for_unit(1.19, "kg") == "1.19"
    assert units.format_qty_for_unit(1.5, "cup") == "1 1/2"
    assert units.format_qty_for_unit(1.5, "tbsp") == "1 1/2"
    assert units.format_qty_for_unit(1.5, None) == "1 1/2"
    # An unknown unit is not a metric one, so it keeps the default rendering.
    assert units.format_qty_for_unit(1.5, "glug") == "1 1/2"


def test_split_amount_does_not_fraction_a_gram_column():
    """The two-column ingredient display reads the same formatter, so a stored
    fractional gram quantity must not come back as "337 1/2" either."""
    assert units.split_amount("337.5 g flour") == ("338", "g", "flour")


def test_pluralize_agrees_with_the_rendered_quantity_not_the_raw_one():
    """format_qty rounds, so 1.0003 prints "1"; comparing the unrounded value
    printed "1 cups" — the exact defect the plural map was added to fix."""
    assert units.format_qty(1.0003) == "1"
    assert units.pluralize_unit("cup", round(1.0003, 2)) == "cup"


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


# --- One canonical key per unit ---------------------------------------------
# The table used to hold BOTH 'g' and 'gram' as canonical keys, so the singular
# spelling passed the membership test and came back unchanged while the plural
# resolved through the alias map. Everything below is a consequence of that, and
# the parametrization is deliberately spelling-exhaustive: the fix is the
# invariant "one key, every other spelling an alias", not two extra aliases.

@pytest.mark.parametrize("spelling,canonical", [
    # weight — the reported pair
    ("gram", "g"), ("grams", "g"), ("gramme", "g"), ("grammes", "g"),
    ("gr", "g"), ("gm", "g"), ("g", "g"),
    ("ounce", "oz"), ("ounces", "oz"), ("oz", "oz"),
    ("kilogram", "kg"), ("kilograms", "kg"), ("kilo", "kg"), ("kgs", "kg"),
    ("pound", "lb"), ("pounds", "lb"), ("lbs", "lb"), ("#", "lb"),
    # volume — the same doubled-key bug, never reported because nobody tried
    ("milliliter", "ml"), ("millilitres", "ml"), ("cc", "ml"),
    ("liter", "l"), ("litre", "l"),
    ("fluid ounce", "fl oz"), ("fluid ounces", "fl oz"), ("fl. oz.", "fl oz"),
    # derived plurals of an abbreviation: 'teaspoons' was listed by hand and
    # worked, 'tsps' was not and returned None
    ("tsps", "tsp"), ("tsp", "tsp"), ("teaspoon", "tsp"), ("teaspoons", "tsp"),
    ("tbsps", "tbsp"), ("tablespoons", "tbsp"),
    # counts
    ("cloves", "clove"), ("pinches", "pinch"), ("bunches", "bunch"),
    ("pkgs", "package"),
])
def test_every_spelling_of_a_unit_canonicalises_to_one_key(spelling, canonical):
    assert units.canonical_unit(spelling) == canonical


def test_no_unit_spelling_is_its_own_second_canonical_key():
    """The structural invariant, asserted directly: a canonical key is the ONLY
    spelling of its unit that canonicalises to itself. Without this, a future
    'teaspoon': 4.92892 row re-introduces the whole defect class silently."""
    for table in (units._VOLUME_ML, units._WEIGHT_G):
        for key, value in table.items():
            twins = [k for k, v in table.items() if v == value and k != key]
            assert not twins, f"{key!r} shares its base amount with {twins!r}"


def test_one_recipe_renders_its_weights_one_way():
    """The user-visible symptom: two lines of the SAME recipe, written with
    different spellings of the gram, scaled to two different notations."""
    assert units.scale_line("1 gram garlic powder", 3) == "3 g garlic powder"
    assert units.scale_line("40 grams parmesan", 3) == "120 g parmesan"


def test_the_singular_spelling_cannot_smuggle_in_a_half_gram():
    """_DECIMAL_UNITS is keyed on 'g', so 'gram' used to slip past it and print
    "337 1/2" — the half-gram fraction that was supposedly already fixed."""
    assert units.format_qty_for_unit(337.5, "gram") == "338"
    assert units.format_qty_for_unit(337.5, "grams") == "338"
    assert units.format_qty_for_unit(0.5, "gram") == "0.5"


def test_a_weight_still_converts_through_every_spelling():
    """Collapsing the table must not cost the conversion: 'ounce' is no longer a
    key in _WEIGHT_G, so to_grams has to reach it through the canonical."""
    assert units.to_grams("2 ounces cheese") == pytest.approx(56.699)
    assert units.to_grams("2 oz cheese") == pytest.approx(56.699)
    assert units.dimension(units.canonical_unit("gram")) == "weight"
    assert units.dimension(units.canonical_unit("fluid ounce")) == "volume"


# --- Fractions typed as separate glyphs -------------------------------------
# The precomposed forms (½ ¼) already parsed, which is why nobody noticed that
# the sub/superscript form — what a word processor's autoformat produces, and
# what survives a copy-paste out of a PDF — parsed to qty=None and silently LOST
# the amount.

@pytest.mark.parametrize("line,qty,unit,rest", [
    ("¹/₂ tsp salt", 0.5, "tsp", "salt"),          # ¹ / ₂  (ASCII /)
    ("¹⁄₂ tsp salt", 0.5, "tsp", "salt"),     # ¹ ⁄ ₂  (U+2044)
    ("¹∕₂ tsp salt", 0.5, "tsp", "salt"),     # ¹ ∕ ₂  (U+2215)
    ("³/₄ cup flour", 0.75, "cup", "flour"),
    ("²/₃ cup sugar", 2 / 3, "cup", "sugar"),
    ("1 ¹/₂ cups flour", 1.5, "cup", "flour"),     # mixed number
    ("½ tsp salt", 0.5, "tsp", "salt"),                      # still works
])
def test_parse_line_reads_a_glyph_built_fraction(line, qty, unit, rest):
    p = units.parse_line(line)
    assert (p["qty"], p["unit"], p["rest"]) == (pytest.approx(qty), unit, rest)


def test_a_superscript_inside_the_food_name_is_left_alone():
    """Normalisation is for reading the QUANTITY. The food text is sliced out of
    the original line, so a superscript that is part of a product name survives
    — and the offsets stay aligned, so no letter is eaten either side of it."""
    assert units.parse_line("2 cups B¹² flour")["rest"] == "2 cups B¹² flour"[7:]
    assert units.parse_line("vitamin B¹² powder")["rest"] == "vitamin B¹² powder"


def test_a_glyph_fraction_scales_like_any_other_quantity():
    """The point of reading it: the user's real paste uses this form, so until
    now those lines did not scale at all."""
    assert units.scale_line("¹/₂ tsp salt", 3) == "1 1/2 tsp salt"


# --- Scaled counts round to something a cook can act on ---------------------
# The policy and its reasoning live in a comment above units.round_count. These
# assert the four tiers behave as that comment claims, through the public
# surface (the rewritten line) rather than against the tier table.

@pytest.mark.parametrize("line,factor,expected", [
    # WHOLE_UP — indivisible. You cannot buy half an egg or half a can.
    ("3 large eggs", 1.5, "5 large eggs"),
    ("1 can chopped tomatoes", 1.5, "2 cans chopped tomatoes"),
    ("2 slices bread", 1.5, "3 slices bread"),
    # ...and never down to nothing: a halved recipe still needs the egg, and
    # buys the whole can it cannot buy half of.
    ("1 egg", 0.5, "1 egg"),
    ("1 can chopped tomatoes", 0.5, "1 can chopped tomatoes"),
    # HALVES — produce a cook cuts. The half here is real information.
    ("1 onion, diced", 1.5, "1 1/2 onions, diced"),
    ("1 lemon", 0.5, "1/2 lemon"),
    ("2 red peppers", 0.75, "1 1/2 red peppers"),
    # UP — cheap and forgiving. More garlic has never ruined a dish.
    ("2 cloves garlic", 1.5, "3 cloves garlic"),
    ("1 clove garlic", 1.5, "2 cloves garlic"),
    ("2 sprigs thyme", 1.6, "4 sprigs thyme"),
    # WHOLE_DOWN — expensive protein by the piece: the tie goes to thrift.
    ("3 chicken breasts", 1.5, "4 chicken breasts"),
    ("2 salmon fillets", 1.5, "3 salmon fillets"),
    # An unrecognised bare count still has to come out whole.
    ("3 tortillas", 1.5, "5 tortillas"),
])
def test_a_scaled_count_is_an_amount_a_cook_can_act_on(line, factor, expected):
    assert units.scale_line(line, factor) == expected


@pytest.mark.parametrize("line,factor,expected", [
    ("2 cups flour", 1.5, "3 cups flour"),
    ("1 tsp salt", 1.5, "1 1/2 tsp salt"),
    ("240 g egg whites", 1.5, "360 g egg whites"),
    ("1 tbsp olive oil", 0.5, "1/2 tbsp olive oil"),
])
def test_a_real_measure_keeps_its_arithmetic(line, factor, expected):
    """Rounding is for counts only. A fraction of a cup or a teaspoon is
    information, and rounding "1 1/2 tsp" to "2 tsp" would be a 33% error in
    the seasoning of the dish."""
    assert units.scale_line(line, factor) == expected


def test_rounding_never_touches_a_quantity_the_cook_typed():
    """Only amounts WE computed are rounded. A cook who typed "1.5 eggs" meant
    it, and an unscaled render must not rewrite their own line."""
    assert units.scale_line("1.5 eggs", 1) == "1.5 eggs"
    assert units.split_amount("1.5 eggs", 1.5) == ("1 1/2", "", "eggs")


def test_round_count_is_idempotent():
    """Scaling runs on every read, and _apply_view rounds the structured
    quantity as well as the text — a second pass must not drift."""
    for value in (0.4, 1.0, 1.5, 4.5, 7.3):
        once = units.round_count(value, None, "eggs")
        assert units.round_count(once, None, "eggs") == once


@pytest.mark.parametrize("line,factor,expected", [
    ("1 onion, diced", 1.5, "1 1/2 onions, diced"),
    ("4 eggs", 0.25, "1 egg"),
    ("1 potato", 2, "2 potatoes"),          # not "2 potatos"
    ("2 red peppers", 0.5, "1 red pepper"),
])
def test_a_counted_food_noun_agrees_with_the_scaled_count(line, factor, expected):
    assert units.scale_line(line, factor) == expected


@pytest.mark.parametrize("line,factor,expected", [
    # The unit carries the number, so the food must NOT be pluralized too.
    ("2 cloves garlic", 1.5, "3 cloves garlic"),
    # A name the catalog does not recognise is never rewritten — this one lost
    # its hyphen the last time something canonicalised text it didn't know.
    ("6 bone-in chicken thighs", 1.5, "9 bone-in chicken thighs"),
    # Mass nouns: "2 chickens"/"2 salmons" is what a general rule would produce.
    ("1 chicken", 2, "2 chicken"),
    ("1 salmon", 2, "2 salmon"),
])
def test_food_nouns_outside_the_countable_classes_are_left_alone(line, factor, expected):
    assert units.scale_line(line, factor) == expected


# --- Measure alternatives, restatements and "optional" ----------------------
# All three arrive buried in the food name, which is where they then travel: to
# the ingredient column, to shopping, and into the shared Food catalog.

@pytest.mark.parametrize("line,food,alt,optional", [
    # The user's real line: the name contains a duplicate of its own amount AND
    # an alternative way to measure it.
    ("1 gram Garlic Powder or ¹/₂ teaspoon (1 g)", "Garlic Powder", "1/2 tsp", False),
    ("½ g peppercorns or ¼ tsp", "peppercorns", "1/4 tsp", False),
    # "optional" hiding behind a trailing bracket — it is anchored at the end,
    # so it was invisible until the bracket came off first.
    ("1 gram Italian Seasoning optional (1 g)", "Italian Seasoning", "", True),
    # ...and the other way round. Both are anchored at the end, so whichever is
    # outermost hides the other; peeling in a fixed order fixed one spelling and
    # silently left the other alone. Found by rendering a long real-world line.
    ("1 gram Italian Seasoning (1 g) optional", "Italian Seasoning", "", True),
    ("1 gram Seasoning Blend, salt-free or ¹/₂ teaspoon (1 g) optional",
     "Seasoning Blend, salt-free", "1/2 tsp", True),
    ("1 tsp chili flakes, optional", "chili flakes", "", True),
    ("(optional) 1 tbsp chili flakes", "chili flakes", "", True),
    # A restatement in a different unit of the same dimension is still a
    # restatement: 8.8 oz IS 250 g.
    ("250 g flour (8.8 oz)", "flour", "", False),
    # Nothing to lift out.
    ("240 g egg whites", "egg whites", "", False),
])
def test_an_amount_restated_in_the_food_name_is_lifted_out_of_it(
        line, food, alt, optional):
    p = units.parse_line(line)
    assert (p["rest"], p["alt"], p["optional"]) == (food, alt, optional)


@pytest.mark.parametrize("line,food", [
    # A different FOOD after "or" is a substitution, not a way to measure the
    # same thing. Restructuring it would be a guess about what the cook meant.
    ("1 cup milk or 240 ml water", "milk or 240 ml water"),
    ("2 cloves garlic or 1 tsp garlic powder", "garlic or 1 tsp garlic powder"),
    # No amount after "or" at all.
    ("1 tsp salt or pepper to taste", "salt or pepper to taste"),
    # A bracket in a DIFFERENT dimension is a genuine weight annotation a cook
    # wants to keep — converting it would need a density, which is a guess this
    # has no business making while deciding to delete someone's text.
    ("2 cups flour (250 g)", "flour (250 g)"),
    # ...and one that is not the same amount is not a restatement either.
    ("250 g flour (500 g bag)", "flour (500 g bag)"),
])
def test_text_that_only_looks_like_an_alternative_is_left_in_the_name(line, food):
    p = units.parse_line(line)
    assert p["rest"] == food
    assert p["alt"] == ""


def test_a_line_with_no_amount_is_handed_back_whole():
    """Cleaning needs an amount column to lift things INTO. With no parsed
    quantity the whole line renders as one string, so stripping a word from it
    would simply lose that word."""
    line = "Salt, to taste, optional"

    p = units.parse_line(line)

    assert p["rest"] == line
    assert p["optional"] is True          # still reported, just not removed


def test_scaling_rewrites_the_alternative_and_drops_the_stale_restatement():
    """A copied alternative beside a tripled amount is worse than none, and
    "(1 g)" beside "3 g" is not stale text — it is a contradiction."""
    line = "1 gram Garlic Powder or ¹/₂ teaspoon (1 g)"

    assert units.scale_line(line, 3) == "3 g Garlic Powder or 1 1/2 tsp"
    assert units.scale_line(line, 0.5) == "0.5 g Garlic Powder or 1/4 tsp"


def test_optional_is_not_said_twice_when_it_was_written_in_front():
    """`prefix` already carries a leading "(optional)" back to the front."""
    assert units.scale_line("(optional) 1 tbsp chili flakes", 2) \
        == "(optional) 2 tbsp chili flakes"


# --- The acid test ----------------------------------------------------------

REAL_RECIPE = [
    "240 g egg whites",
    "40 g parmesan",
    "1 gram Garlic Powder or ¹/₂ teaspoon (1 g)",
    "½ g peppercorns or ¼ tsp",
    "Oil or 1 tsps oil for the pan",
]


@pytest.mark.parametrize("factor,expected", [
    (0.5, [
        "120 g egg whites",
        "20 g parmesan",
        "0.5 g Garlic Powder or 1/4 tsp",
        "0.2 g peppercorns or 1/8 tsp",
        "Oil or 1 tsps oil for the pan",
    ]),
    (1.5, [
        "360 g egg whites",
        "60 g parmesan",
        "2 g Garlic Powder or 3/4 tsp",
        "0.8 g peppercorns or 3/8 tsp",
        "Oil or 1 tsps oil for the pan",
    ]),
    (3, [
        "720 g egg whites",
        "120 g parmesan",
        "3 g Garlic Powder or 1 1/2 tsp",
        "2 g peppercorns or 3/4 tsp",
        "Oil or 1 tsps oil for the pan",
    ]),
])
def test_a_real_pasted_recipe_scales_into_lines_a_human_would_write(factor, expected):
    """The whole point, asserted on the actual lines that exposed every defect
    in this module: one spelling of the gram, the superscript fraction read
    rather than dropped, the alternative measure scaled alongside the amount,
    the restated "(1 g)" gone, and the unmeasured line left exactly alone.

    Every string here is the line as it must appear to a cook. If one of them
    stops reading like English, this test is the place that says so.
    """
    assert [units.scale_line(line, factor) for line in REAL_RECIPE] == expected
