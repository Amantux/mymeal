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


def test_the_lexicon_is_case_insensitive():
    """MUTATION GUARD. `_FOLDED` is keyed by lowercase seed names while `fold()`
    preserves case, so a title-case food used to miss entirely:
    is_known("Flour") was False while is_known("flour") was True.

    This is not hypothetical casing pedantry — recipe text is overwhelmingly
    title-case ("2 cups Flour", "Olive Oil"), and every caller read the miss as
    "unknown food" rather than as a bug. It silently weakened the
    material-boundary rule too, since an unrecognised head word cannot be
    compared against its qualifier.
    """
    from app.services import food_resolve

    for text in ("Flour", "FLOUR", "Olive Oil", "Green Beans"):
        assert food_resolve.is_known(text), f"{text!r} not recognised"
        assert food_resolve.match_key(text) == text.lower()


def test_an_unknown_food_is_still_unknown_whatever_its_case():
    """The fix must not turn the lexicon into a yes-machine."""
    from app.services import food_resolve

    for text in ("Quattro Formaggi Base", "quattro formaggi base"):
        assert not food_resolve.is_known(text)


# --- the widened catalog ------------------------------------------------------
# "Wegman's Avocados are Avocados. Organic Raspberries are Raspberries."
#
# These failed for ONE reason, and it was not the qualifier machinery: the
# resolver anchors on a known HEAD and strips what precedes it, and neither
# avocado nor raspberry was in the catalog. Coverage is the fix, and ADR-0001
# already says coverage is a safety property here, not just a quality one.


@pytest.mark.parametrize("raw,food,qualifier", [
    ("Wegman's Avocados", "avocado", "wegman s"),     # the owner's first example
    ("Organic Raspberries", "raspberry", "organic"),  # ...and the second
    ("organic avocado", "avocado", "organic"),
    ("Wegmans Raspberries", "raspberry", "wegmans"),
    ("Trader Joe's Blueberries", "blueberry", "trader joe s"),
    ("organic kale", "kale", "organic"),
    ("Italian broccoli", "broccoli", "italian"),
    ("baby spinach", "spinach", "baby"),
    ("Spanish chorizo", "chorizo", "spanish"),
])
def test_a_brand_or_provenance_strips_off_a_newly_known_food(raw, food, qualifier):
    assert split(raw) == (food, qualifier)
    assert why(raw) == "split"


@pytest.mark.parametrize("raw", [
    "avocado", "raspberry", "strawberry", "blueberry", "ketchup", "mayonnaise",
    "broccoli", "cauliflower", "cabbage", "kale", "leek", "shallot", "asparagus",
    "beetroot", "parsnip", "pumpkin", "mango", "pineapple", "grape", "pear",
    "peach", "cherry", "fig", "raisin", "olive", "quinoa", "couscous", "barley",
    "polenta", "spaghetti", "tortilla", "cheddar", "mozzarella", "feta",
    "halloumi", "ricotta", "lamb", "turkey", "duck", "sausage", "chorizo",
    "ham", "cod", "haddock", "mackerel", "anchovy", "crab", "mussels", "squid",
    "tempeh", "mustard", "tahini", "miso", "hummus", "pesto", "oregano", "sage",
    "cardamom", "saffron", "cayenne", "sumac", "molasses",
])
def test_a_household_staple_is_in_the_catalog(raw):
    """The seed list was 135 entries and whole aisles were missing. Each of
    these used to come back "unknown", which costs a consolidation on every
    shopping list that mentions it."""
    assert fr.is_known(raw), f"{raw!r} is not in the catalog"
    assert why(raw) == "exact"


@pytest.mark.parametrize("raw,food", [
    ("arugula", "rocket"),
    ("bok choy", "pak choi"),
    ("brinjal", "aubergine"),
    ("capsicum", "bell pepper"),
    ("rutabaga", "swede"),
    ("beet", "beetroot"),
    ("mayo", "mayonnaise"),
    ("catsup", "ketchup"),
    ("red pepper flakes", "chilli flake"),
    ("apple cider vinegar", "cider vinegar"),
    ("parmigiano reggiano", "parmesan"),
    ("flaxseed", "flax seed"),
])
def test_a_regional_name_for_a_new_food_resolves_to_one_food(raw, food):
    assert split(raw) == (food, "")
    assert why(raw) == "alias"


# --- the widened catalog must not cross a material boundary -------------------

@pytest.mark.parametrize("raw,trap", [
    ("cheddar cheese", "cheese"),       # a named cheese is not "any cheese"
    ("feta cheese", "cheese"),
    ("mozzarella cheese", "cheese"),
    ("parmesan cheese", "cheese"),
    ("goat cheese", "cheese"),
    ("blue cheese", "cheese"),
    ("rye flour", "flour"),             # both grains, both gluten
    ("bread flour", "flour"),
    ("cake flour", "flour"),
    ("spelt flour", "flour"),
    ("rice noodles", "noodle"),         # gluten-free vs wheat
    ("egg noodles", "noodle"),
    ("rice milk", "milk"),
    ("cashew milk", "milk"),
    ("coconut cream", "cream"),
    ("green lentils", "lentils"),
    ("red pepper", "pepper"),
    ("cherry tomato", "tomato"),
    ("plum tomato", "tomato"),
])
def test_a_new_compound_never_collapses_into_its_generic(raw, trap):
    """The danger a bigger catalog adds is MORE chances for a substring match to
    cross a boundary, so each of these pins the specific collapse it must not
    make. A generic answer here is a wrong purchase: cheddar for feta, plain
    flour for rye, wheat noodles for rice ones."""
    food, qualifier = split(raw)
    assert food != trap, f"{raw!r} collapsed to {trap!r} via {why(raw)}"
    assert qualifier == ""


@pytest.mark.parametrize("raw,head", [
    ("halloumi cheese", "cheese"),   # the classification edge, not a seeded compound
    ("ricotta cheese", "cheese"),
    ("gruyere cheese", "cheese"),
    ("lamb stock", "stock"),         # meat vs liquid
    ("turkey stock", "stock"),
    ("mango yoghurt", "yoghurt"),    # fruit vs dairy
    ("raspberry yoghurt", "yoghurt"),
])
def test_a_new_entry_earns_its_refusal_from_the_material_edge_alone(raw, head):
    """MUTATION GUARD for guard 2 *with the widened catalog in place*. None of
    these phrases is a seed, so guard 1 cannot save them — the refusal comes
    only from the new entry differing materially from the head. Disable
    ``_differs_materially`` and every one collapses into the generic, which is
    what makes this the test that proves a bigger catalog did not weaken the
    boundary. It is also the evidence for the named-cheese classification
    claim: no "<name> cheese" compound is seeded for these three."""
    assert why(raw) == "material", f"{raw!r} was caught by {why(raw)}, not the edge"
    assert split(raw) == (fr.normalize_text(raw), "")
    assert split(raw)[0] != head


def test_a_named_cheese_is_classified_apart_from_generic_cheese():
    """How the collapse above is actually prevented, stated once: a named cheese
    gets its own classification, so guard 2 separates it from `cheese` without a
    compound being seeded for every variety. `parmesan` was filed under "dairy"
    — identical to `cheese` — so "parmesan cheese" split to plain `cheese`."""
    assert fr.SEED_FOODS["parmesan"][0] == "cheese"
    assert fr.SEED_FOODS["cheese"][0] == "dairy"
    assert split("grated parmesan")[0] == "parmesan"


def test_bone_in_is_functional_so_a_seeded_cut_cannot_rewrite_it():
    """MUTATION GUARD. Seeding "chicken thigh" gave the resolver a head it never
    had, which made "bone-in chicken thighs" splittable for the first time — and
    ADR-0001 protects that exact string from being rewritten, because bone-in
    and boneless are not interchangeable. Remove "bone in" from
    FUNCTIONAL_QUALIFIERS and this fails."""
    for raw in ("bone-in chicken thighs", "boneless chicken thighs",
                "skinless chicken breast"):
        assert fr.normalize(raw)[1] == "", f"{raw!r} split off a qualifier"
        assert why(raw) == "functional"
    # ...while the plain cut still resolves, which is the point of seeding it.
    assert split("chicken thighs") == ("chicken thigh", "")


# --- deliberate OMISSIONS, and why --------------------------------------------

@pytest.mark.parametrize("word,protects", [
    ("sesame", "sesame oil"),
    ("corn", "corn flour"),
    ("sunflower", "sunflower oil"),
    ("rapeseed", "rapeseed oil"),
    ("cider", "cider vinegar"),
    ("sherry", "sherry vinegar"),
])
def test_a_word_that_only_ever_qualifies_an_oil_or_vinegar_stays_unknown(word, protects):
    """MUTATION GUARD, and the most counter-intuitive rule in this file.

    ``units._density_for``'s head-noun fallback fails OPEN on an unknown
    qualifier — ADR-0001 records that as deliberate, because failing closed
    would refuse "sunflower oil" and "maple syrup". The corollary is that
    TEACHING the lexicon one of these words makes the fallback start refusing:
    measured, seeding "sesame" and "corn" cost 64 density lookups, including
    `sesame oil` and `cornstarch`, which the ADR lists as wins.

    So these words are absent on purpose and the COMPOUND is seeded instead.
    Adding one back to SEED_FOODS silently removes a weight estimate.
    """
    from app.services import units

    assert not fr.is_known(word), (
        f"{word!r} was added to the catalog; it costs {protects!r} its density")
    assert units._density_for(protects) is not None, f"{protects} lost its density"


@pytest.mark.parametrize("word,swallows", [
    ("beans", "organic garbanzo beans"),
    ("syrup", "corn syrup"),
])
def test_a_generic_head_that_would_swallow_unseeded_varieties_stays_unknown(
        word, swallows):
    """A bare generic is only safe as a head when guard 2 can separate the
    varieties beneath it. Every pulse shares one classification and no
    allergen, so "beans" swallowed anything not individually seeded —
    "organic garbanzo beans" resolved to plain `beans` rather than chickpeas,
    because a prefix stops the ALIAS matching and the suffix loop only
    considers seed foods as heads. Pulses are not substitutable."""
    assert not fr.is_known(word)
    assert fr.food_key(swallows) != word


# --- catalog-wide invariants ---------------------------------------------------

def test_every_entry_has_a_classification_and_an_allergen_tuple():
    """Both are load-bearing: they are the only things guard 2 compares, so a
    malformed row silently stops refusing."""
    for name, entry in fr.SEED_FOODS.items():
        assert isinstance(entry, tuple) and len(entry) == 2, name
        classification, allergens = entry
        assert classification and isinstance(classification, str), name
        assert isinstance(allergens, tuple), name
        assert all(isinstance(a, str) and a for a in allergens), name


def test_the_allergen_vocabulary_stays_closed():
    """A typo ("nut" for "nuts") creates a brand-new material that differs from
    everything, so the guard starts refusing splits at random. Keeping the set
    closed turns that typo into a failing test."""
    assert {a for _c, al in fr.SEED_FOODS.values() for a in al} == {
        "dairy", "egg", "gluten", "nuts", "peanut", "sesame", "soy", "fish",
        "shellfish",
    }


def test_coconut_carries_no_allergen_because_this_is_the_material_lexicon():
    """MUTATION GUARD for the trap ADR-0001 calls out. foodcore files coconut
    under tree nuts, because its question is FDA labelling. This file's question
    is "may a substring match cross from here to there", and coconut is a drupe
    — it is not almond. Both are right for their own question and must stay
    separate; `food_reference` has the seam comment.

    Giving coconut the nut allergen here would make "coconut milk" and
    "coconut oil" behave like almond's, which is a different claim entirely.
    """
    assert fr.SEED_FOODS["coconut"] == ("fruit", ())
    assert fr.SEED_FOODS["coconut milk"] == ("plant milk", ())


def test_a_compound_seed_is_never_shadowed_by_a_shorter_seed():
    """Guard 1 must win for every multi-word entry whose tail is also a food.
    Seeding a compound IS how you protect it (ADR-0001), so an entry that does
    not come back whole is a silent collapse — "parmesan cheese" to `cheese`
    was exactly this, and it shipped."""
    collapsed = {}
    for name in fr.SEED_FOODS:
        food, qualifier, reason = fr.normalize(name)
        if food != name or qualifier:
            collapsed[name] = (food, qualifier, reason)
    assert collapsed == {}


def test_no_seed_name_is_also_an_alias_key():
    """An alias for a name that is itself a food is a contradiction: one folded
    key would have two answers, and which one wins depends on dict order."""
    assert set(fr.SEED_ALIASES) & set(fr.SEED_FOODS) == set()


@pytest.mark.parametrize("text,density", [
    ("sesame oil", 0.92),
    ("corn flour", 0.53),
    ("cornstarch", 0.53),
    ("olive oil", 0.92),
    ("maple syrup", 1.37),
    ("chicken stock", 1.0),
    ("peanut butter", 1.09),
    ("whole wheat flour", 0.53),
    ("sunflower oil", 0.92),
])
def test_the_widened_catalog_keeps_the_density_lookups_it_had(text, density):
    """A bigger catalog means more known qualifiers, and every newly-known
    qualifier is a new chance for the density fallback to refuse a crossing it
    used to allow. These are the lookups ADR-0001 names as the wins of the
    head-noun fallback, so they are pinned against exactly that drift."""
    from app.services import units

    assert units._density_for(text) == density


@pytest.mark.parametrize("raw,was,now", [
    ("bread flour", "flour", "bread flour"),
    ("green lentils", "lentils", "green lentils"),
    ("red pepper", "pepper", "red pepper"),
    ("green pepper", "pepper", "green pepper"),
    ("sunflower oil", "oil", "sunflower oil"),
    ("parmesan cheese", "cheese", "parmesan cheese"),
    ("whole wheat flour", "flour", "wheat flour"),
])
def test_a_variety_that_used_to_collapse_now_keeps_its_identity(raw, was, now):
    """The seven resolutions this change deliberately MOVES, pinned so the move
    is a decision rather than a side effect. Each used to resolve to the generic
    on the left, which is a wrong purchase — a recipe wanting bread flour,
    parmesan or a red pepper is not served by plain flour, any cheese, or a
    green one.

    "whole wheat flour" is the subtlest: the compound was seeded all along, but
    ``match_key`` strips "whole" as a preparation word, so the key became plain
    `flour` and the seeded row was unreachable — the same class of dead row as
    the yogurt/yoghurt key mismatch in ADR-0001.
    """
    assert fr.food_key(raw) != was
    assert fr.match_key(raw) == now
