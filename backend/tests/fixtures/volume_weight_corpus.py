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
    ("breadcrumbs",      "cup", 108.0, "174928", "the PLAIN row; 172806 is the SEASONED one at 120"),
    ("peanut butter",    "cup", 258.0, "172470", "smooth"),
    ("cocoa powder",     "cup",  86.0, "169593", "unsweetened"),
    ("raisins",          "cup", 145.0, "168165", ""),
    ("cream cheese",     "cup", 232.0, "173418", ""),
    ("sour cream",       "cup", 230.0, "171257", ""),
    ("yogurt",           "cup", 245.0, "171284", "plain whole milk"),
    # ⚠️ KNOWN GAP, kept deliberately red rather than reclassified. Unlike
    # almonds and walnuts above, the line NAMES its form -- "shredded" -- so
    # the answer is not ambiguous, it is 113 g. The engine still refuses,
    # because a qualified portion row is not reachable from the words a cook
    # wrote (foodcore's `convert` and `portion_grams` disagree about qualified
    # portions). Reclassifying this as ambiguous would hide a real defect
    # behind a correct-sounding label, which is the one thing this corpus is
    # for. 3 cup weights on one food: shredded 113 / diced 132 / melted 244.
    ("shredded cheddar", "cup", 113.0, "173414", "the text pins the form; the engine cannot use it"),
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
    # Moved here from RESOLVED on the evidence. Both were asserted at their
    # WHOLE/CHOPPED weight because that is what King Arthur tabulates, but USDA
    # publishes no unqualified cup row for either -- every row names a form,
    # and the forms are not close:
    ("almonds", "cup", [92.0, 95.0, 108.0, 143.0],
     "whole 143 / slivered 108 / ground 95 / sliced 92 -- 55% spread, no "
     "unqualified row. 'A cup of almonds' is not a measurement"),
    ("walnuts", "cup", [28.0, 80.0, 100.0, 117.0, 120.0],
     "pieces 120 / chopped 117 / shelled 100 / ground 80 / in-shell yield 28 "
     "-- a 329% spread, the widest in the dataset"),
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
    # Third food where the tolerance hid a WRONG-FOOD answer, and the tightest
    # yet: whole wheat is 120 and plain is 125, so falling back to plain flour
    # was 4% off -- comfortably inside tolerance while being a different grain
    # with different hydration and gluten. The seeded row was unreachable
    # because match_key strips "whole", so this looked like a pass for as long
    # as nobody asked whether it was the right food.
    ("whole wheat flour", "flour", "cup", 0.04, "120 vs 125 -- different grain, 4% apart"),
    ("brown sugar",    "sugar",  "cup", 0.09, "220 packed vs 200 granulated"),
    ("powdered sugar", "sugar",  "cup", 0.39, "120 vs 200 -- aeration"),
    ("cooked rice",    "rice",   "cup", 0.14, "158 vs 185 -- water replaces air"),
    ("quinoa",         "cooked quinoa", "cup", 0.08, "170 uncooked vs 185 cooked"),
]
