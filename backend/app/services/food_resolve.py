"""Canonical food identity: "Vietnamese Cinnamon" → cinnamon + "Vietnamese".

The pure half of the food resolver — lexicon only, **no database**, safe to call
from hot read paths (``units._density_for``, ``conversions.food_term``,
``inventory._ingredient_covered``). The DB-backed half lives alongside it and
ranks against a household's own ``Food`` rows.

See ``docs/adr/0001-canonical-foods-and-qualifiers.md``. The short version:

A split is **emergent**, not a curated phrase map. "Vietnamese cinnamon" ends
with the known food "cinnamon", so it splits and the leftover text is the
qualifier. Longest suffix wins, so "extra virgin olive oil" resolves to
``olive oil`` and never to ``oil``.

Three guards decide when a split is refused, and the order matters:

1. the whole phrase is itself a known food (``peanut butter``, ``sour cream``,
   ``sweet potato``, ``green onion``);
2. the qualifier is itself a food that differs **materially** from the head —
   different classification or allergens. This is Edibl's rule ("substring never
   crosses an allergen edge", its ADR-0003) and it generalises: ``cashew
   butter``, ``oat milk``, ``rice vinegar``, ``chicken stock`` and ``olive oil``
   are all refused without appearing in any list;
3. the qualifier is **functional** — if buying the canonical instead would change
   what you cook, it does not split (``self-raising flour``, ``double cream``);
Seeding a compound IS how you protect it: ``sweet potato`` and ``green onion``
are seed foods, so guard 1 keeps them whole. An earlier version also carried a
separate ``PROTECTED_COMPOUNDS`` list; removing it failed no test, because every
entry was already a seed. One list, not two.

The failure mode is therefore always "didn't normalize", never "merged two
different ingredients". An omission from the seed data costs a missed
consolidation; it cannot put the wrong thing in your basket.
"""
from __future__ import annotations

import re
import unicodedata

# --- seed vocabulary ---------------------------------------------------------
# name -> (classification, allergens). Classification and allergens are what
# guard 2 compares, so two foods that must never collapse into one another need
# to differ in at least one of them. This is data, not logic: adding a food is a
# one-line change and omitting one degrades to "not normalized".
_D = "dairy"
_N = "nuts"
_G = "gluten"

SEED_FOODS: dict[str, tuple[str, tuple[str, ...]]] = {
    # dairy — the cluster the allergen guard exists for
    "milk": ("dairy", (_D,)),
    "butter": ("dairy", (_D,)),
    "cream": ("dairy", (_D,)),
    "sour cream": ("dairy", (_D,)),
    "double cream": ("dairy", (_D,)),
    "heavy cream": ("dairy", (_D,)),
    "cream cheese": ("dairy", (_D,)),
    "condensed milk": ("dairy", (_D,)),
    "evaporated milk": ("dairy", (_D,)),
    "buttermilk": ("dairy", (_D,)),
    "yoghurt": ("dairy", (_D,)),
    "cheese": ("dairy", (_D,)),
    "parmesan": ("dairy", (_D,)),
    "egg": ("egg", ("egg",)),
    # plant milks and nut butters — same shape, different allergens on purpose
    "almond milk": ("plant milk", (_N,)),
    "oat milk": ("plant milk", (_G,)),
    "soy milk": ("plant milk", ("soy",)),
    "coconut milk": ("plant milk", ()),
    "peanut butter": ("spread", ("peanut",)),
    "almond butter": ("spread", (_N,)),
    "almond": ("nut", (_N,)),
    "cashew": ("nut", (_N,)),
    "peanut": ("legume", ("peanut",)),
    "walnut": ("nut", (_N,)),
    "coconut": ("fruit", ()),
    "desiccated coconut": ("baking", ()),
    # spices — the motivating case
    "cinnamon": ("spice", ()),
    "nutmeg": ("spice", ()),
    "paprika": ("spice", ()),
    "smoked paprika": ("spice", ()),
    "cumin": ("spice", ()),
    "turmeric": ("spice", ()),
    "black pepper": ("spice", ()),
    "white pepper": ("spice", ()),
    "chilli": ("spice", ()),
    "vanilla": ("spice", ()),
    "vanilla extract": ("baking", ()),
    "bay leaf": ("herb", ()),
    "salt": ("seasoning", ()),
    # herbs
    "parsley": ("herb", ()),
    "coriander": ("herb", ()),
    "basil": ("herb", ()),
    "thyme": ("herb", ()),
    "rosemary": ("herb", ()),
    "mint": ("herb", ()),
    "dill": ("herb", ()),
    # grains, flours, baking
    "flour": ("grain", (_G,)),
    "plain flour": ("grain", (_G,)),
    "self raising flour": ("grain", (_G,)),
    "whole wheat flour": ("grain", (_G,)),
    "corn flour": ("grain", ()),
    "rice": ("grain", ()),
    "wild rice": ("grain", ()),
    "brown rice": ("grain", ()),
    "oats": ("grain", (_G,)),
    "pasta": ("grain", (_G,)),
    "orzo": ("grain", (_G,)),
    "bread": ("grain", (_G,)),
    "breadcrumbs": ("grain", (_G,)),
    "sugar": ("baking", ()),
    "brown sugar": ("baking", ()),
    "icing sugar": ("baking", ()),
    "honey": ("baking", ()),
    "baking powder": ("baking", ()),
    "baking soda": ("baking", ()),
    "cream of tartar": ("baking", ()),
    "yeast": ("baking", ()),
    "cocoa": ("baking", ()),
    "chocolate": ("baking", ()),
    "dark chocolate": ("baking", ()),
    "milk chocolate": ("baking", (_D,)),
    "white chocolate": ("baking", (_D,)),
    # fats and liquids
    "oil": ("fat", ()),
    "olive oil": ("fat", ()),
    "sesame oil": ("fat", ("sesame",)),
    "vegetable oil": ("fat", ()),
    "coconut oil": ("fat", ()),
    "vinegar": ("condiment", ()),
    "rice vinegar": ("condiment", ()),
    "balsamic vinegar": ("condiment", ()),
    "soy sauce": ("condiment", ("soy", _G)),
    "stock": ("liquid", ()),
    "chicken stock": ("liquid", ()),
    "vegetable stock": ("liquid", ()),
    "water": ("liquid", ()),
    "wine": ("alcohol", ()),
    "white wine": ("alcohol", ()),
    "red wine": ("alcohol", ()),
    # produce
    "onion": ("vegetable", ()),
    "red onion": ("vegetable", ()),
    "green onion": ("vegetable", ()),
    "garlic": ("vegetable", ()),
    "potato": ("vegetable", ()),
    "sweet potato": ("vegetable", ()),
    "tomato": ("vegetable", ()),
    "carrot": ("vegetable", ()),
    "celery": ("vegetable", ()),
    "pepper": ("vegetable", ()),
    "mushroom": ("vegetable", ()),
    "spinach": ("vegetable", ()),
    "courgette": ("vegetable", ()),
    "aubergine": ("vegetable", ()),
    "cucumber": ("vegetable", ()),
    "lettuce": ("vegetable", ()),
    "green beans": ("vegetable", ()),
    "peas": ("vegetable", ()),
    "sweet corn": ("vegetable", ()),
    "lemon": ("fruit", ()),
    "lime": ("fruit", ()),
    "orange": ("fruit", ()),
    "apple": ("fruit", ()),
    "banana": ("fruit", ()),
    "ginger": ("vegetable", ()),
    # pulses and proteins
    "black beans": ("pulse", ()),
    "red lentils": ("pulse", ()),
    "lentils": ("pulse", ()),
    "chickpeas": ("pulse", ()),
    "chicken": ("meat", ()),
    "chicken breast": ("meat", ()),
    "beef": ("meat", ()),
    "pork": ("meat", ()),
    "bacon": ("meat", ()),
    "salmon": ("fish", ("fish",)),
    "tuna": ("fish", ("fish",)),
    "prawns": ("shellfish", ("shellfish",)),
    "tofu": ("protein", ("soy",)),
    # tea/coffee
    "tea": ("drink", ()),
    "green tea": ("drink", ()),
    "coffee": ("drink", ()),
}

# Regional and spelling variants. An alias resolves to its canonical without any
# splitting — this is what makes "aubergine"/"eggplant" one food.
SEED_ALIASES: dict[str, str] = {
    "eggplant": "aubergine",
    "zucchini": "courgette",
    "cilantro": "coriander",
    "scallion": "green onion",
    "spring onion": "green onion",
    "yogurt": "yoghurt",
    "yoghourt": "yoghurt",
    "confectioners sugar": "icing sugar",
    "powdered sugar": "icing sugar",
    "all purpose flour": "plain flour",
    "cornstarch": "corn flour",
    "corn starch": "corn flour",
    "bicarbonate of soda": "baking soda",
    "baking bicarbonate": "baking soda",
    "caster sugar": "sugar",
    "granulated sugar": "sugar",
    "aubergines": "aubergine",
    "shrimp": "prawns",
    "garbanzo beans": "chickpeas",
    "coriander leaf": "coriander",
}

# Guard 3. Words that make a qualifier FUNCTIONAL: buying the canonical instead
# would change what you cook. Note these overlap with `conversions._NOISE` — that
# list is correct for a *weight* cache (salted and unsalted butter weigh the
# same) and must never be merged with this one.
FUNCTIONAL_QUALIFIERS: frozenset[str] = frozenset({
    "self raising", "selfraising", "self rising", "plain", "whole wheat",
    "wholemeal", "strong", "gluten free", "unsalted", "salted", "double",
    "heavy", "single", "sour", "clotted", "condensed", "evaporated", "skimmed",
    "semi skimmed", "dark", "white", "smoked", "desiccated", "instant",
    "quick cook", "dried", "fresh", "frozen", "raw", "cooked", "toasted",
    "low fat", "full fat", "reduced fat", "unsweetened", "sweetened",
})

# Provenance adjectives — near-universally "where it came from" rather than
# "what it is". The one generic layer the design allows itself.
NATIONALITY_QUALIFIERS: frozenset[str] = frozenset({
    "vietnamese", "ceylon", "saigon", "korean", "sichuan", "szechuan",
    "italian", "greek", "thai", "french", "spanish", "mexican", "japanese",
    "chinese", "indian", "turkish", "moroccan", "lebanese", "sicilian",
    "cornish", "scottish", "irish", "welsh", "danish", "swiss", "german",
})

_PAREN_RE = re.compile(r"\([^)]*\)")
_NONWORD_RE = re.compile(r"[^a-z0-9 ]+")
_SPACE_RE = re.compile(r"\s+")


def singularize(word: str) -> str:
    """Crude plural folding. Edibl's version only strips a trailing "s", which
    cannot reach "tomatoes" -> "tomato" or "potatoes" -> "potato"; the -oes and
    -ies forms are common enough in a recipe app to be worth the two extra rules.
    Kept compatible with Edibl for everything its rule already handled."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("oes", "ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize_text(raw: str) -> str:
    """Lowercase, drop preparation after the first comma, strip punctuation."""
    low = (raw or "").strip().lower()
    # Fold accents to ASCII rather than letting the punctuation rule turn them
    # into spaces — "jalapeño" must not become "jalape o".
    low = "".join(c for c in unicodedata.normalize("NFKD", low)
                  if not unicodedata.combining(c))
    # Everything after a comma is preparation, not identity.
    low = low.split(",")[0]
    low = _PAREN_RE.sub(" ", low)
    low = _NONWORD_RE.sub(" ", low)
    return _SPACE_RE.sub(" ", low).strip()


def fold(phrase: str) -> str:
    """A phrase with its LAST word singularized.

    Food names are head-final, so folding only the final word is enough to make
    "tomatoes" and "tomato", or "large eggs" and "large egg", meet — while
    leaving genuinely-plural seed names ("green beans", "chickpeas") intact,
    because they get folded the same way on both sides.
    """
    if not phrase:
        return phrase
    words = phrase.split()
    words[-1] = singularize(words[-1])
    return " ".join(words)


# Every seed name and alias, keyed by its folded form, so a lookup only has to
# fold the query. Built once at import; the module is pure data plus functions.
_FOLDED: dict[str, str] = {}
for _name in SEED_FOODS:
    _FOLDED.setdefault(fold(_name), _name)
for _alias, _canonical in SEED_ALIASES.items():
    _FOLDED.setdefault(fold(_alias), _canonical)


def _lookup(phrase: str) -> str | None:
    """The canonical name for a phrase, or None."""
    return _FOLDED.get(fold(phrase))


def _material(name: str) -> tuple[str, frozenset[str]] | None:
    entry = SEED_FOODS.get(name)
    return (entry[0], frozenset(entry[1])) if entry else None


def _differs_materially(qualifier: str, head: str) -> bool:
    """True when the qualifier is itself a food unlike the head food.

    This is the guard that stops "peanut butter" becoming butter and "oat milk"
    becoming milk, without either appearing in a list — the same rule Edibl
    states as "substring never crosses an allergen edge" (its ADR-0003).
    """
    qname = _lookup(qualifier)
    if not qname:
        return False
    qmat, hmat = _material(qname), _material(head)
    if not qmat or not hmat:
        return False
    return qmat[0] != hmat[0] or qmat[1] != hmat[1]


def _is_functional(qualifier: str) -> bool:
    if qualifier in FUNCTIONAL_QUALIFIERS:
        return True
    # Multi-word qualifiers are checked as phrases too, so "extra virgin" is not
    # shredded into "extra" + "virgin" — the same longest-first idea as _UNIT_KEYS.
    return any(word in FUNCTIONAL_QUALIFIERS for word in qualifier.split())


# Longest first, so "olive oil" is tried before "oil" and "black pepper" before
# "pepper". Without this, "extra virgin olive oil" resolves to plain oil.
_SEED_BY_LENGTH = sorted(SEED_FOODS, key=lambda n: (-len(n), n))


def normalize(raw: str) -> tuple[str, str, str]:
    """``(food, qualifier, matched_on)`` for a free-text ingredient name.

    ``food`` is the canonical name, ``qualifier`` the variety (possibly ""), and
    ``matched_on`` says which rule fired — ``exact``, ``alias``, ``split``,
    ``protected``, ``material``, ``functional`` or ``unknown``. Callers surface
    it, and the tests assert on it so a case cannot pass for the wrong reason.

    Never raises, and never returns an empty food for non-empty input: an
    unrecognised phrase comes back unchanged, which is the safe outcome.
    """
    text = normalize_text(raw)
    if not text:
        return "", "", "unknown"

    # Guard 1 — the whole phrase is itself a food.
    direct = _lookup(text)
    if direct:
        return direct, "", ("exact" if fold(direct) == fold(text) else "alias")

    folded = fold(text)
    for head in _SEED_BY_LENGTH:
        suffix = " " + fold(head)
        if not folded.endswith(suffix):
            continue
        qualifier = folded[: -len(suffix)].strip()
        if not qualifier:
            continue
        # Guard 2 — the qualifier is a materially different food.
        if _differs_materially(qualifier, head):
            return text, "", "material"
        # Guard 3 — the qualifier changes what you cook.
        if _is_functional(qualifier):
            return text, "", "functional"
        return head, qualifier, "split"

    return text, "", "unknown"


def food_key(raw: str) -> str:
    """The canonical name alone — the cache/consolidation key.

    Replaces ``conversions.food_term``'s last-word rule, which mapped "peanut
    butter" to "butter" and let a learned "1 stick butter = 113 g" be applied to
    peanut butter.
    """
    return normalize(raw)[0]
