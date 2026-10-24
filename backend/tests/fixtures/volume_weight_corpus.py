"""Labelled corpus for VOLUME -> WEIGHT. Held out from implementers.

⚠️ DO NOT HAND-TYPE AN FDC ID INTO THIS FILE. Every id here was derived by
searching descriptions and then verified by tools/validate_corpora.py --labels,
because hand-typing failed twice in a row: ids typed from memory pointed at a
MARS candy bar, millet, pumpkin seeds, and (in this very file, caught by the
label audit) the COOKED quinoa row where the uncooked one was meant. The audit
is not ceremony -- it is the only reason those four are not still wrong.

Every expectation here agrees between TWO INDEPENDENT sources:

  A. USDA SR Legacy portion rows, located by searching descriptions.
  B. King Arthur Baking's published cup-weight chart (the de facto culinary
     standard) plus standard dairy/liquid figures.

Agreement within 12% -> ground truth, expectation is USDA's (citable).
Disagreement         -> it does NOT become an expectation. See AMBIGUOUS.

Why two sources: a corpus built from the dataset the code reads cannot
distinguish "the code is wrong" from "my expectation is wrong" -- it just
re-asserts the dataset. Cross-validation is what makes a failure here
actionable. Three FDC ids hand-typed during this work pointed at a candy bar,
millet, and pumpkin seeds; searching plus a second source caught all three.

TOLERANCE. Cup weights are a scoop, not a constant: USDA and King Arthur
differ by up to 12% on flour depending on whether it was sifted. Asserting to
the gram would encode one agency's scooping technique as physics. RESOLVED
cases allow 12%.
"""
from __future__ import annotations

#: (food_text, unit, expected_grams, usda_fdc_id, note)
#:
#: fdc id is recorded so a future dataset bump can be DIFFED against these
#: rather than re-derived from scratch -- if USDA restates a portion, the
#: corpus should move deliberately and visibly.
RESOLVED: list[tuple[str, str, float, str, str]] = [
    ("flour",            "cup", 125.0, "168894", "all-purpose, bleached"),
    ("bread flour",      "cup", 137.0, "168896", "distinct from plain flour"),
    ("whole wheat flour", "cup", 120.0, "168893", "pattern had to be widened to find this"),
    ("sugar",            "cup", 200.0, "169655", "granulated"),
    ("brown sugar",      "cup", 220.0, "168833", "PACKED -- the qualifier is the number"),
    ("powdered sugar",   "cup", 120.0, "169656", "unsifted"),
    ("butter",           "cup", 227.0, "173410", "exactly 2 sticks; both sources agree to the gram"),
    ("milk",             "cup", 244.0, "171265", "whole"),
    ("water",            "cup", 237.0, "174158", "near-definitional, sanity anchor"),
    ("honey",            "cup", 339.0, "169640", "densest common ingredient"),
    ("maple syrup",      "cup", 315.0, "169661", ""),
    ("molasses",         "cup", 337.0, "168820", "found as 'Molasses', NOT 'Syrups, cane, molasses'"),
    ("olive oil",        "cup", 216.0, "171413", ""),
    ("rice",             "cup", 185.0, "168877", "RAW long-grain white"),
    ("cooked rice",      "cup", 158.0, "169753", "must not collapse into raw rice"),
    ("oats",             "cup",  81.0, "173904", "rolled, dry"),
    ("cornmeal",         "cup", 122.0, "169697", "whole-grain yellow; degermed is 157"),
    ("quinoa",           "cup", 170.0, "168874", "UNCOOKED -- 168917 is the COOKED row at 185"),
    ("barley",           "cup", 200.0, "170284", "pearled, raw"),
    ("lentils",          "cup", 192.0, "172420", "raw"),
    ("breadcrumbs",      "cup", 120.0, "172806", "USDA only has SEASONED dry crumbs; 11% off plain"),
    ("almonds",          "cup", 143.0, "170567", "whole"),
    ("walnuts",          "cup", 117.0, "170187", "chopped"),
    ("peanut butter",    "cup", 258.0, "172470", "smooth"),
    ("cocoa powder",     "cup",  86.0, "169593", "unsweetened"),
    ("raisins",          "cup", 145.0, "168165", ""),
    ("cream cheese",     "cup", 232.0, "173418", ""),
    ("sour cream",       "cup", 230.0, "171257", ""),
    ("yogurt",           "cup", 245.0, "171284", "plain whole milk"),
    ("shredded cheddar", "cup", 113.0, "173414", "one food, 3 cup weights: shredded 113 / diced 132 / melted 244"),
    ("parmesan",         "cup", 100.0, "171247", "grated"),
]

#: Genuinely ambiguous: USDA publishes SEVERAL cup weights for the bare term
#: and they are all correct for different forms. Confidently returning any one
#: number is WRONG -- the engine should decline, or resolve only when the text
#: pins the form.
#:
#: "1 cup chickpeas" spans 152 g (canned, drained and rinsed) to 240 g (canned,
#: with liquid) -- a 58% spread. Picking the unqualified row, which is what a
#: naive resolver does, silently gets the with-liquid figure.
AMBIGUOUS: list[tuple[str, str, list[float], str]] = [
    ("chickpeas", "cup", [152.0, 164.0, 200.0, 240.0],
     "raw 200 / cooked 164 / canned+liquid 240 / drained+rinsed 152"),
]

#: USDA has the FOOD but publishes no cup portion for it, so volume->weight is
#: genuinely unanswerable from this dataset. The correct behaviour is an
#: explicit unresolved, NEVER a plausible-looking guess.
#:
#: This is a third of the point of the corpus. Only 2,582 of 7,793 SR Legacy
#: foods (33%) carry a cup portion at all, so "no answer" is the COMMON case
#: and has to be a first-class, tested outcome rather than an edge case.
UNRESOLVABLE_FROM_DATASET: list[tuple[str, str, str]] = [
    ("tahini", "cup", "4 tahini entries in SR Legacy, not one cup portion"),
]

#: Cross-dimension requests that must REFUSE, not guess. `convert()`'s contract
#: already does this; the corpus pins it so a coverage-widening change cannot
#: quietly relax it into "well, a cup of water is 237 g, so...".
MUST_REFUSE: list[tuple[str, str, str, str]] = [
    ("", "cup", "g", "no food -- a bare cup has no weight without a density"),
    ("saffron", "cup", "g", "unknown food must not fall back to a generic density"),
]


#: Pairs that must NOT return the same weight, with the gap USDA publishes.
#:
#: ⚠️ This list exists because the RESOLVED tolerance MASKED a real bug. USDA
#: puts bread flour at 137 g/cup and plain flour at 125 -- a 9.6% gap, which is
#: INSIDE the 12% scoop tolerance. So `grams_per("cup", "bread flour")`
#: returning 125 (it silently falls back to plain flour) passed the absolute
#: check while being exactly the error the case was written to catch.
#:
#: The lesson generalises: a tolerance wide enough to absorb scooping technique
#: is wider than the distance between some foods the corpus must tell apart.
#: Absolute-value cases cannot police near neighbours; only an explicit
#: "these two must differ" case can. Any future tolerance widening must add the
#: new near-neighbour pairs here in the same change.
#:
#: Returning None for the variant is ALSO a failure here. Declining is an
#: honest answer to "what does a cup of X weigh", but it is not evidence the
#: engine can tell two foods apart, and this list is only about that.
DISTINCT_PAIRS: list[tuple[str, str, str, float, str]] = [
    ("bread flour",    "flour",  "cup", 0.09, "137 vs 125 -- protein content; INSIDE scoop tolerance"),
    ("brown sugar",    "sugar",  "cup", 0.09, "220 packed vs 200 granulated"),
    ("powdered sugar", "sugar",  "cup", 0.39, "120 vs 200 -- aeration"),
    ("cooked rice",    "rice",   "cup", 0.14, "158 vs 185 -- water replaces air"),
    ("quinoa",         "cooked quinoa", "cup", 0.08, "170 uncooked vs 185 cooked"),
]
