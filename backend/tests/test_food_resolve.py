"""The trap table. This is the gate the canonical-food feature has to pass.

The dangerous failure is not "didn't normalize" — it's "merged two different
ingredients". Every MUST-NOT-SPLIT case therefore asserts **which guard** caught
it, so a case can't pass for the wrong reason and silently regress when the seed
data is edited.

See docs/adr/0001-canonical-foods-and-qualifiers.md.
"""
import pytest

from app.services import food_resolve as fr


def split(raw):
    food, qualifier, _ = fr.normalize(raw)
    return food, qualifier


def why(raw):
    return fr.normalize(raw)[2]


# --- MUST SPLIT ---------------------------------------------------------------

@pytest.mark.parametrize("raw,food,qualifier", [
    ("Vietnamese Cinnamon", "cinnamon", "vietnamese"),      # the motivating case
    ("Ceylon cinnamon", "cinnamon", "ceylon"),
    ("Saigon cinnamon", "cinnamon", "saigon"),
    ("Sea Salt", "salt", "sea"),
    ("kosher salt", "salt", "kosher"),
    ("Maldon sea salt", "salt", "maldon sea"),              # multi-word qualifier
    ("San Marzano tomatoes", "tomato", "san marzano"),      # + plural fold
    ("Greek yoghurt", "yoghurt", "greek"),
    ("Italian parsley", "parsley", "italian"),
    ("flat leaf parsley", "parsley", "flat leaf"),
    ("large free-range eggs", "egg", "large free range"),
    ("chopped walnuts", "walnut", "chopped"),
])
def test_a_variety_splits_off_its_canonical_food(raw, food, qualifier):
    assert split(raw) == (food, qualifier)
    assert why(raw) == "split"


def test_the_longest_food_wins_so_a_variety_of_olive_oil_is_not_plain_oil():
    """"extra virgin olive oil" → oil would let sesame oil be substituted."""
    assert split("extra virgin olive oil") == ("olive oil", "extra virgin")


def test_a_qualifier_in_front_of_a_two_word_food_still_splits():
    assert split("freshly ground black pepper") == ("black pepper", "freshly ground")


# --- MUST NOT SPLIT: guard 1, the phrase is itself a food ----------------------

@pytest.mark.parametrize("raw", [
    "peanut butter", "sour cream", "black pepper", "olive oil", "coconut milk",
    "condensed milk", "evaporated milk", "buttermilk", "vanilla extract",
    "corn flour", "chicken stock", "smoked paprika", "desiccated coconut",
    "white chocolate", "dark chocolate", "rice vinegar", "white wine",
])
def test_a_compound_that_is_its_own_ingredient_is_left_whole(raw):
    food, qualifier = split(raw)
    assert (food, qualifier) == (raw, "")
    assert why(raw) == "exact"


# --- MUST NOT SPLIT: guard 2, the material boundary ---------------------------

@pytest.mark.parametrize("raw,head", [
    ("cashew butter", "butter"),      # nuts vs dairy
    ("walnut oil", "oil"),            # nut vs fat
    ("almond flour", "flour"),        # nut vs grain
    ("coconut sugar", "sugar"),       # fruit vs baking
    ("coconut oil", "oil"),           # seeded outright
    ("almond milk", "milk"),          # nuts vs dairy
])
def test_a_qualifier_that_is_a_different_kind_of_food_refuses_the_split(raw, head):
    """Edibl's rule: substring never crosses an allergen/classification edge.
    None of these appear in any list — the boundary catches them."""
    food, qualifier = split(raw)
    if why(raw) == "material":
        assert (food, qualifier) == (fr.normalize_text(raw), "")
    else:
        # If it wasn't the boundary, it must not have silently split to the head.
        assert food != head, f"{raw!r} collapsed to {head!r} via {why(raw)}"


@pytest.mark.parametrize("raw", ["oat milk", "soy milk", "almond milk"])
def test_a_plant_milk_never_becomes_dairy_milk(raw):
    """The single most quoted example in Edibl's ADR-0003."""
    assert split(raw)[0] != "milk"


def test_the_allergen_edge_is_what_stops_peanut_butter_becoming_butter():
    """Stated explicitly because it is the case that kills a head-noun rule:
    'unsalted butter' and 'peanut butter' differ only in vocabulary."""
    assert split("peanut butter")[0] == "peanut butter"
    assert split("cashew butter")[0] != "butter"
    assert why("cashew butter") == "material"


# --- MUST NOT SPLIT: guard 3, functional qualifiers ---------------------------

@pytest.mark.parametrize("raw", [
    "self raising flour", "whole wheat flour", "unsalted butter",
    "salted butter", "double cream", "heavy cream", "skimmed milk",
    "smoked salmon", "dried oregano", "frozen peas",
])
def test_a_qualifier_that_changes_what_you_cook_refuses_the_split(raw):
    """Buying plain flour when the recipe said self-raising ruins the bake."""
    food, qualifier = split(raw)
    assert qualifier == "", f"{raw!r} split off {qualifier!r}"
    assert why(raw) in ("exact", "functional", "unknown")


# --- MUST NOT SPLIT: same-class compounds, kept whole by being seeded ---------

@pytest.mark.parametrize("raw,trap", [
    ("sweet potato", "potato"),
    ("sweet corn", "corn"),
    ("green onion", "onion"),
    ("green beans", "beans"),
    ("green tea", "tea"),
    ("wild rice", "rice"),
    ("brown rice", "rice"),
    ("black beans", "beans"),
    ("red lentils", "lentils"),
    ("baking powder", "powder"),
    ("baking soda", "soda"),
    ("cream of tartar", "cream"),
    ("bay leaf", "leaf"),
])
def test_a_same_class_compound_is_kept_whole(raw, trap):
    """Both halves are vegetables (or both grains), so neither the allergen nor
    the classification edge can tell them apart. Seeding the compound is what
    keeps it whole — which is also why a separate protected list was removed as
    dead code."""
    food, qualifier = split(raw)
    assert qualifier == ""
    assert food != trap
    assert why(raw) == "exact"


# --- aliases ------------------------------------------------------------------

@pytest.mark.parametrize("raw,food", [
    ("eggplant", "aubergine"),
    ("zucchini", "courgette"),
    ("cilantro", "coriander"),
    ("scallions", "green onion"),
    ("spring onion", "green onion"),
    ("yogurt", "yoghurt"),
    ("all purpose flour", "plain flour"),
    ("cornstarch", "corn flour"),
    ("powdered sugar", "icing sugar"),
])
def test_a_regional_name_resolves_to_one_food(raw, food):
    assert split(raw) == (food, "")
    assert why(raw) == "alias"


# --- normalization mechanics --------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Cinnamon, ground", "cinnamon"),
    ("cinnamon (Vietnamese)", "cinnamon"),
    ("  CINNAMON  ", "cinnamon"),
    ("tomatoes", "tomato"),
    ("EGGS", "egg"),
])
def test_preparation_casing_and_plurals_reach_the_same_food(raw, expected):
    assert split(raw)[0] == expected


@pytest.mark.parametrize("raw", ["", "   ", ",,,", "()"])
def test_empty_input_is_not_a_crash(raw):
    assert fr.normalize(raw) == ("", "", "unknown")


def test_an_unknown_ingredient_comes_back_unchanged():
    """The safe failure: not normalized, never mis-merged."""
    assert split("quargelk\u00e4se") == ("quargelkase", ""), "accents fold, not split"
    assert why("quargelk\u00e4se") == "unknown"


def test_food_key_replaces_the_last_word_rule():
    """The live bug this fixes: food_term('peanut butter') was 'butter', so a
    learned '1 stick butter = 113 g' was applied to peanut butter."""
    assert fr.food_key("peanut butter") == "peanut butter"
    assert fr.food_key("coconut milk") != fr.food_key("evaporated milk")
    assert fr.food_key("rice vinegar") != "rice"


def test_the_seed_data_has_no_alias_pointing_at_a_missing_food():
    """An alias to a food that doesn't exist resolves to a name nothing else
    knows — a silent dead end."""
    missing = {a: c for a, c in fr.SEED_ALIASES.items() if c not in fr.SEED_FOODS}
    assert missing == {}


def test_the_allergen_edge_matters_even_when_the_classification_matches():
    """rice flour and flour are BOTH grains — the allergen list is the only thing
    stopping a coeliac being told rice flour is flour. Found by mutation: dropping
    allergens from the comparison broke no test until this one existed."""
    assert fr.SEED_FOODS["rice"][0] == fr.SEED_FOODS["flour"][0] == "grain"
    assert split("rice flour") == ("rice flour", "")
    assert why("rice flour") == "material"


def test_the_functional_and_weight_noise_lists_are_not_the_same_thing():
    """conversions._NOISE strips 'unsalted' because salted and unsalted butter
    weigh the same. Merging that list into this one would make the shopping list
    tell you to buy plain flour for a self-raising recipe."""
    from app.services import conversions

    assert "unsalted" in conversions._NOISE
    assert "unsalted" in fr.FUNCTIONAL_QUALIFIERS
    assert conversions._NOISE != fr.FUNCTIONAL_QUALIFIERS
    # The proof that they must stay separate:
    assert fr.normalize("unsalted butter")[1] == ""
