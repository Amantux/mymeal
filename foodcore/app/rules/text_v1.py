"""Text-matching data shared by the allergen and dietary screens — DATA ONLY.

``STOP_WORDS`` is the list of tokens that carry quantity, preparation or grade
rather than identity. It exists for ONE purpose: deciding whether a leftover,
unmatched token means "we did not understand this ingredient" (→ ``unknown``)
or just "this word was 'finely'".

Getting it wrong is asymmetric, and the asymmetry is in our favour: a word
missing from this list makes an ingredient look unrecognised, which produces
``unknown``, which a strict profile excludes. The cost is a false exclusion,
never a false clearance.

⚠️ This is NOT ``food_resolve.PREPARATION_WORDS`` and must not be merged with
it. That list answers "do these two texts mean the same food?" and so contains
``plain`` (plain and self-raising flour weigh the same). Here, dropping a word
means "ignore it for screening", and the two lists have different safety
properties — the same reason ``food_resolve`` keeps ``PREPARATION_WORDS`` and
``FUNCTIONAL_QUALIFIERS`` apart.
"""
from __future__ import annotations

VERSION = "text/2026.10.1"

STOP_WORDS: frozenset[str] = frozenset({
    # quantities and units
    "g", "gram", "grams", "kg", "kilo", "kilos", "mg", "ml", "l", "litre",
    "litres", "liter", "liters", "cup", "cups", "tbsp", "tbs", "tsp",
    "teaspoon", "teaspoons", "tablespoon", "tablespoons", "oz", "ounce",
    "ounces", "lb", "lbs", "pound", "pounds", "pint", "pints", "quart",
    "fl", "pinch", "pinches", "dash", "splash", "drizzle", "knob", "clove",
    "cloves", "sprig", "sprigs", "stalk", "stalks", "stick", "sticks",
    "head", "heads", "bunch", "bunches", "handful", "handfuls", "slice",
    "slices", "piece", "pieces", "can", "cans", "tin", "tins", "jar",
    "jars", "packet", "packets", "pack", "packs", "box", "bag", "bottle",
    "sachet", "cube", "cubes", "portion", "portions", "serving", "servings",
    "x",
    # size and grade
    "large", "small", "medium", "big", "little", "extra", "jumbo", "baby",
    "mini", "thin", "thick", "good", "quality", "best", "nice", "any",
    "some", "few", "free", "range", "organic", "wild", "farmed",
    "low", "full", "reduced", "half", "light", "lean",
    # preparation
    "fresh", "freshly", "dried", "chopped", "finely", "roughly", "coarsely",
    "diced", "sliced", "thinly", "thickly", "minced", "grated", "shredded",
    "torn", "crushed", "cubed", "halved", "quartered", "peeled", "deseeded",
    "seeded", "cored", "trimmed", "rinsed", "washed", "drained", "cooked",
    "uncooked", "raw", "ripe", "unripe", "melted", "softened", "soft",
    "firm", "ground", "whole", "beaten", "whisked", "sifted", "sieved",
    "toasted", "roasted", "boiled", "steamed", "fried", "grilled", "baked",
    "warmed", "chilled", "frozen", "defrosted", "thawed", "canned",
    "tinned", "jarred", "bottled", "pitted", "stoned", "zested", "juiced",
    "divided", "separated", "reserved", "optional", "cut", "lengthways",
    "crosswise", "julienned", "smoked", "cured", "pickled", "marinated",
    "room", "temperature", "warm", "cold", "hot", "boiling", "lukewarm",
    "unsalted", "salted", "sweetened", "unsweetened", "skinless",
    "boneless", "skin", "bone", "in", "on", "off",
    # cuts and portions — identity lives in the head word ("salmon", "pork"),
    # so leaving these unlisted made "salmon fillet" look unrecognised.
    "fillet", "fillets", "loin", "rump", "joint", "rack", "shank", "chop",
    "chops", "drumstick", "drumsticks", "wing", "wings", "leg", "legs",
    "thigh", "thighs", "breast", "breasts", "shoulder", "flesh", "skinned",
    "deboned", "butterflied", "trimmings", "scraps",
    # glue words
    "a", "an", "the", "of", "to", "and", "or", "plus", "with", "without",
    "for", "from", "into", "per", "about", "approx", "approximately",
    "around", "each", "more", "less", "as", "needed", "required", "taste",
    "garnish", "serve", "served", "topping", "filling", "if", "you", "your",
    "my", "it", "them", "this", "that", "very", "just", "only", "etc",
    "plain",
})
