"""Labelled corpus for ingredient NAME ABSTRACTION. Held out from implementers.

Two opposite failure modes, and a corpus that only tests one of them is worse
than no corpus because it rewards a fix that breaks the other:

  STRIP    "Wegman's Avocados" is avocado. Brand, grade, size, freshness and
           pack words are not identity. Failing to strip fragments the catalog
           into one entry per supermarket.

  PRESERVE "almond milk" is NOT milk. A modifier that changes the MATERIAL may
           never be dropped -- that is ADR-0001, and the blast radius is
           allergen screening, not just a wrong weight.

Each case also pins the EXPECTED REASON (`normalize()`'s third return value),
because the right answer for the wrong reason is a latent bug: "almond milk"
staying intact via `unknown` (nothing matched) looks identical to staying
intact via `material` (the boundary guard fired) until someone adds "milk" to
the catalog and the first one silently starts stripping.

`expect_reason` is a SET: more than one route can be legitimately correct
(`exact` vs `alias` depends on catalog spelling, which is not what is under
test here).

Labels were cross-validated against USDA SR Legacy -- see
tools/validate_corpora.py. A canonical term no reference dataset knows is a
term I invented, and it gets fixed, not asserted.
"""
from __future__ import annotations

# Fields: (input, expected_food, expect_reason, note)
#
# expected_food is compared case-insensitively after the same text
# normalisation the resolver applies, so "Avocado" vs "avocado" is not a
# failure -- spelling of the canonical entry is the catalog's business.

STRIP: list[tuple[str, str, set[str], str]] = [
    # --- the user's own examples -------------------------------------------
    ("Wegman's Avocados",        "avocado",     {"split", "exact", "alias"}, "possessive brand + plural"),
    ("Organic Raspberries",      "raspberry",   {"split", "exact", "alias"}, "grade word + plural"),
    ("Trader Joe's Bananas",     "banana",      {"split", "exact", "alias"}, "multi-word brand"),
    # --- brand shapes ------------------------------------------------------
    ("Kirkland Signature Olive Oil", "olive oil", {"split", "exact", "alias"}, "two-word brand, two-word food"),
    ("365 Whole Foods Butter",   "butter",      {"split", "exact", "alias"}, "numeral-led brand"),
    ("Heinz Ketchup",            "ketchup",     {"split", "exact", "alias"}, "brand that is near-synonymous"),
    ("Philadelphia Cream Cheese", "cream cheese", {"split", "exact", "alias"}, "brand + COMPOUND food"),
    # --- grade / freshness / size -----------------------------------------
    ("large eggs",               "egg",         {"split", "exact", "alias"}, "size + plural"),
    ("ripe avocado",             "avocado",     {"split", "exact", "alias"}, "ripeness"),
    ("extra large onions",       "onion",       {"split", "exact", "alias"}, "two-word size"),
    # --- preparation: cut state is not identity ---------------------------
    ("chopped walnuts",          "walnut",      {"split", "exact", "alias"}, "cut state"),
    # --- plural / punctuation ---------------------------------------------
    ("Tomatoes",                 "tomato",      {"exact", "alias", "split"}, "bare plural, -oes"),
    ("CARROTS",                  "carrot",      {"exact", "alias", "split"}, "caseless"),
]

PRESERVE: list[tuple[str, str, set[str], str]] = [
    # --- ADR-0001 material boundary: the allergen-critical ones ------------
    ("almond milk",    "almond milk",    {"material", "exact", "alias"}, "NOT milk -- dairy allergen"),
    ("oat milk",       "oat milk",       {"material", "exact", "alias"}, "NOT milk"),
    ("coconut milk",   "coconut milk",   {"material", "exact", "alias"}, "NOT milk"),
    ("soy milk",       "soy milk",       {"material", "exact", "alias"}, "NOT milk -- soy allergen"),
    ("peanut butter",  "peanut butter",  {"material", "exact", "alias"}, "NOT butter -- peanut allergen"),
    ("almond flour",   "almond flour",   {"material", "exact", "alias"}, "NOT flour -- not gluten, not 125g/cup"),
    ("coconut oil",    "coconut oil",    {"material", "exact", "alias"}, "NOT oil generically -- solid at 20C"),
    # --- functional boundary: same material, DIFFERENT behaviour ----------
    ("brown sugar",    "brown sugar",    {"functional", "exact", "alias"}, "220 vs 200 g/cup, and packs"),
    ("powdered sugar", "icing sugar",    {"functional", "exact", "alias"}, "120 vs 200 g/cup; catalog spells it icing sugar"),
    ("cooked rice",    "cooked rice",    {"functional", "exact", "alias"}, "158 vs 185 g/cup"),
    ("bread flour",    "bread flour",    {"functional", "exact", "alias"}, "different protein AND weight"),
    ("self-raising flour", "self raising flour", {"functional", "exact", "alias"}, "leavening; hyphen is normalised to a space"),
    ("condensed milk", "condensed milk", {"functional", "exact", "alias"}, "same material, 306 g/cup"),
    ("greek yogurt",   "greek yogurt",   {"functional", "exact", "alias", "unknown"}, "strained -- denser; currently survives via `unknown`, which is a WEAK pass: add 'yogurt' splitting and it would regress silently"),
    # `fresh` and `unsalted` live here, not in STRIP. Mislabelled first time
    # round: they ARE functional for shopping (fresh vs frozen spinach is a
    # different purchase) and the weight question is handled separately -- see
    # WEIGHT_EQUIVALENT below. Shopping identity and weight identity are two
    # questions and one `normalize()` cannot answer both.
    ("fresh spinach",   "fresh spinach",   {"functional", "exact", "alias"}, "fresh vs frozen is a different purchase"),
    ("unsalted butter", "unsalted butter", {"functional", "exact", "alias"}, "a different purchase, but the SAME weight"),
    # --- compounds that merely LOOK like brand+food -----------------------
    ("sweet potato",   "sweet potato",   {"material", "functional", "exact", "alias"}, "not a sweet potato"),
    ("green onion",    "green onion",    {"material", "functional", "exact", "alias"}, "distinct produce item"),
    ("cream cheese",   "cream cheese",   {"material", "functional", "exact", "alias"}, "not cheese, not cream"),
    ("buttermilk",     "buttermilk",     {"exact", "alias", "unknown"},   "single token -- must not split"),
]

#: Terms whose CANONICAL form the catalog is expected to know. Separate metric
#: from normalisation on purpose: "Wegman's Avocados" -> "avocado" is the
#: normaliser working, and `is_known("avocado")` being False is the CATALOG
#: being thin. Conflating them sends someone to rewrite a resolver that is
#: already correct.
MUST_BE_KNOWN: list[str] = [
    "avocado", "raspberry", "banana", "ketchup", "spinach", "egg", "onion",
    "tomato", "carrot", "walnut", "butter", "olive oil", "cream cheese",
    "blueberry", "strawberry", "cucumber", "zucchini", "mushroom", "garlic",
    "lemon", "lime", "mayonnaise", "mustard", "soy sauce", "honey", "oats",
]


#: Pairs that must weigh THE SAME, whatever `normalize()` does with them.
#:
#: This is the other half of the functional-qualifier design and the reason
#: `fresh spinach` staying intact is not a bug. "unsalted butter" is a distinct
#: thing to BUY and an identical thing to WEIGH, so the shopping lane keeps the
#: qualifier while the conversion lane sees through it. Asserting it here means
#: a future change to FUNCTIONAL_QUALIFIERS cannot quietly break weighing in
#: order to fix shopping, which is precisely the trade someone would make
#: without this list.
WEIGHT_EQUIVALENT: list[tuple[str, str, str]] = [
    ("unsalted butter", "butter",  "salt is ~1.5% of mass and not a density change"),
    ("salted butter",   "butter",  "same"),
    # ("chopped walnuts", "walnuts") was here and is gone: bare "walnuts" has
    # five qualified cup rows spanning 329% and no unqualified one, so it MUST
    # refuse (see AMBIGUOUS in the volume-weight corpus). A pair whose base is
    # required to decline cannot test equivalence -- it would have asserted
    # that a correct refusal equals a correct answer.
    # Base must RESOLVE for an equivalence pair to mean anything. "chopped
    # onion" was the first replacement and was no better than what it replaced
    # -- onion does not convert by cup either, so the pair compared None with
    # None, which the vacuity guard correctly refuses to call a pass.
    ("plain flour", "flour", "British name for all-purpose; one food, one weight"),
    ("whole milk", "milk", "unqualified milk IS whole milk"),
]
