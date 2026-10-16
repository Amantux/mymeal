"""Measurement parsing, scaling, and volume↔weight conversion.

Recipe ingredient lines are free text ("1 1/2 cups flour"). This module parses
the leading quantity + unit so the app can (a) SCALE a recipe to a target
serving count and (b) where a food's density is known, present WEIGHT-based
measurements regardless of how the recipe was written ("1 cup flour" → "125 g
flour"), converting under the covers. Pure functions, no app database, never
raises.

Weights come from three places, in this order: definitional physics for mass
units, then the shared foodcore reference dataset when an operator has imported
one (``food_reference`` — the only module that knows foodcore exists), then the
hand-written density table below. The reference dataset is read-only, optional
and absent by default; with no dataset configured this module behaves exactly
as it did before foodcore existed.
"""
from __future__ import annotations

import math
import re
from fractions import Fraction

# --- Canonical units -> base amount. Volume base = millilitre, weight = gram --
#
# EXACTLY ONE canonical key per unit. That invariant is load-bearing, and the
# table used to break it: 'g' and 'gram' both sat here as keys, so
# canonical_unit('grams') came back 'g' (via the alias table) while
# canonical_unit('gram') passed the membership test below and came back
# unchanged. Three user-visible defects fell out of that one inconsistency:
#
#   * one recipe rendered two ways — scaling "1 gram garlic powder" gave
#     "3 grams" while "40 grams parmesan" gave "120 g";
#   * _DECIMAL_UNITS is keyed on 'g', so the singular spelling slipped past it
#     and printed "337 1/2" — half a gram, as a vulgar fraction;
#   * shopping consolidation grouped on the unit, so 'g' and 'gram' were two
#     different units and you came home with parmesan twice.
#
# Adding 'gram' and 'ounce' as aliases would have fixed the two spellings that
# were reported and left every other doubled key ('milliliter', 'cc', 'liter',
# 'fluid ounce') live. Collapsing the table to one key each fixes the class.
_VOLUME_ML = {
    "ml": 1.0,
    "l": 1000.0,
    "tsp": 4.92892,
    "tbsp": 14.7868,
    "fl oz": 29.5735,
    "cup": 236.588,
    "pint": 473.176, "quart": 946.353, "gallon": 3785.41,
}
_WEIGHT_G = {
    "g": 1.0,
    "kg": 1000.0,
    "oz": 28.3495,
    "lb": 453.592,
}

# Count-ish units that appear constantly in scraped recipes. They are
# deliberately absent from _VOLUME_ML and _WEIGHT_G: there is no honest
# millilitre or gram value for "1 clove", and inventing one would make
# to_grams() quietly wrong. dimension() therefore returns None for these, so
# scaling multiplies the count and weight conversion declines — which is the
# correct behaviour, not a limitation.
_COUNT_UNITS = {
    "pinch", "dash", "handful", "clove", "slice", "can", "tin", "package",
    "stick", "sprig", "bunch", "head", "jar", "bottle", "packet",
}

# Canonical key -> the OTHER spellings a recipe writes it as. Irregular forms
# only: plurals are derived below, so adding a spelling never means remembering
# to add its plural too. That omission is what left canonical_unit('tsps')
# returning None while 'teaspoons' worked.
_SPELLINGS: dict[str, tuple[str, ...]] = {
    "ml": ("millilitre", "milliliter", "cc"),
    "l": ("litre", "liter"),
    "tsp": ("teaspoon", "ts"),
    "tbsp": ("tablespoon", "tbs", "tbl"),
    "fl oz": ("fluid ounce", "floz", "fl. oz.", "fl. oz"),
    "cup": ("c",),
    "pint": ("pt",),
    "quart": ("qt",),
    "gallon": ("gal",),
    "g": ("gram", "gramme", "gr", "gm"),
    "kg": ("kilogram", "kilo"),
    "oz": ("ounce",),
    "lb": ("pound", "#"),
    "package": ("pkg",),
}


def _plural_forms(word: str) -> tuple[str, ...]:
    """Plural spellings to accept for a unit word.

    Single characters are skipped deliberately: 'g'/'l'/'c'/'#' would pluralize
    to 'gs'/'ls'/'cs', which nobody writes, and every alias widens the surface
    the unit matcher scans. Everything else gets -s, plus -es after a sibilant
    ('pinch' -> 'pinches', 'oz' -> 'ozes').

    Deliberately generous rather than linguistically exact — it also mints
    'pinchs' and 'ozes'. An alias nobody ever types costs nothing; a MISSING one
    is the defect this derivation exists to prevent.
    """
    if len(word) < 2:
        return ()
    if word.endswith(("ch", "sh", "s", "x", "z")):
        return (word + "s", word + "es")
    return (word + "s",)


# Every spelling -> its canonical key, derived once at import. Built from the
# declaration above plus a regular plural for each spelling AND each canonical,
# so "cups", "teaspoons", "tsps", "grams" and "cloves" all resolve without
# being listed by hand.
_ALIASES: dict[str, str] = {}
for _canonical in list(_VOLUME_ML) + list(_WEIGHT_G) + sorted(_COUNT_UNITS):
    for _form in (_canonical, *_plural_forms(_canonical)):
        _ALIASES.setdefault(_form, _canonical)
for _canonical, _spellings in _SPELLINGS.items():
    for _spelling in _spellings:
        for _form in (_spelling, *_plural_forms(_spelling)):
            _ALIASES.setdefault(_form, _canonical)

# Approximate densities (grams per millilitre) for common pantry foods, matched
# by keyword against the ingredient text. Enough for a sensible weight readout;
# not laboratory-grade. Only used when a volume needs to become a weight.
_DENSITY_G_PER_ML = {
    "water": 1.0, "milk": 1.03, "cream": 1.0, "stock": 1.0, "broth": 1.0,
    "oil": 0.92, "olive oil": 0.92, "butter": 0.96, "honey": 1.42, "syrup": 1.37,
    "flour": 0.53, "sugar": 0.85, "brown sugar": 0.90, "powdered sugar": 0.56,
    "salt": 1.22, "rice": 0.85, "cocoa": 0.52, "cocoa powder": 0.52, "oats": 0.41,
    "yogurt": 1.03, "ketchup": 1.14,

    # Compounds that are their OWN substance, listed because the head-noun
    # fallback in _density_for deliberately refuses them (a nut butter is not
    # butter). Without a real value they would silently show no weight at all,
    # and three of these were previously served their head's density, which was
    # simply wrong — condensed milk is 24% denser than milk, and peanut butter
    # 13% denser than butter.
    "peanut butter": 1.09, "almond butter": 1.05, "cashew butter": 1.05,
    "condensed milk": 1.28, "evaporated milk": 1.07, "buttermilk": 1.03,
    "almond milk": 1.03, "oat milk": 1.04, "soy milk": 1.03,
    "coconut milk": 0.98, "coconut cream": 1.00, "coconut oil": 0.92,
    "chicken stock": 1.0, "beef stock": 1.0, "vegetable stock": 1.0,
    "rice vinegar": 1.01, "rice wine": 0.99, "vinegar": 1.01,
    "almond flour": 0.42, "cream of tartar": 0.90,
}

_UNICODE_FRACTIONS = {
    "½": Fraction(1, 2), "⅓": Fraction(1, 3), "⅔": Fraction(2, 3),
    "¼": Fraction(1, 4), "¾": Fraction(3, 4), "⅕": Fraction(1, 5),
    "⅛": Fraction(1, 8), "⅜": Fraction(3, 8), "⅝": Fraction(5, 8), "⅞": Fraction(7, 8),
}

# A fraction typed as separate glyphs — "¹⁄₂", "¹/₂", "³/₄" — rather than as one
# precomposed character. Blogs and YouTube descriptions are full of these,
# because that is what a word processor's autoformat produces and what survives
# a copy-paste out of a PDF. The precomposed forms above already parsed, so the
# defect was invisible until a real paste used the superscript form and the
# quantity was silently LOST: "¹/₂ tsp salt" parsed to qty=None and the whole
# line, half-teaspoon and all, fell through as unmeasured food text.
#
# ⚠️ Every mapping here is one character to one character, and parse_line relies
# on that: it matches against the normalised copy but slices the food text out of
# the ORIGINAL, so the two must stay aligned offset-for-offset. A mapping that
# changed length (say "½" -> "1/2") would shift every later offset and start
# eating letters off the food name.
_NUMERAL_FORMS = str.maketrans({
    # superscript digits (U+2070, U+00B9-00B3, U+2074-2079)
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    # subscript digits (U+2080-2089)
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    # FRACTION SLASH (U+2044) and DIVISION SLASH (U+2215), which look like "/"
    # and mean "/" but are not "/"
    "⁄": "/", "∕": "/",
})


def _normalize_numerals(s: str) -> str:
    """Rewrite sub/superscript digits and fraction slashes as ASCII, preserving
    length (see the warning on _NUMERAL_FORMS)."""
    return s.translate(_NUMERAL_FORMS)

# Longest unit spellings first so "fl oz" matches before "oz".
_UNIT_KEYS = sorted(
    set(_VOLUME_ML) | set(_WEIGHT_G) | set(_ALIASES) | _COUNT_UNITS,
    key=len, reverse=True,
)

# Words a recipe puts in front of a quantity. Stripped before matching so
# "About 2 cups milk" parses; the original line is never rewritten, only the
# structured quantity/unit are derived from it.
_APPROXIMATORS = (
    "approximately", "approx.", "approx", "about", "around", "roughly",
    "a scant", "scant", "heaping", "heaped", "generous", "a generous",
)

# "1 (14.5 oz) can diced tomatoes" — the bracketed pack size sits between the
# count and the unit and blocked the unit match. Skipped when looking for the
# unit; left in the food text, where a cook expects to read it.
_LEADING_PAREN_RE = re.compile(r"^\s*\([^)]*\)\s*")


def canonical_unit(raw: str) -> str | None:
    u = (raw or "").strip().lower().rstrip(".")
    if not u:
        return None
    u = _ALIASES.get(u, u)
    # Count units are canonical too, even though they convert to nothing —
    # returning None for them would leave "2 cloves garlic" with no unit at all,
    # so the word stayed stuck in the food name and the quantity scaled without
    # its measure. dimension() still returns None for them, which is what keeps
    # weight conversion honest.
    return u if u in _VOLUME_ML or u in _WEIGHT_G or u in _COUNT_UNITS else None


def dimension(unit: str | None) -> str | None:
    if unit in _VOLUME_ML:
        return "volume"
    if unit in _WEIGHT_G:
        return "weight"
    return None


def _parse_number(token: str):
    """Parse '2', '2.5', '1/2', '1 1/2', or a leading unicode fraction → float."""
    token = token.strip()
    if not token:
        return None
    total = 0.0
    # Leading unicode fraction (possibly after a whole number, e.g. "1½").
    m = re.match(r"^(\d+)?\s*([" + "".join(_UNICODE_FRACTIONS) + r"])$", token)
    if m:
        whole = int(m.group(1)) if m.group(1) else 0
        return whole + float(_UNICODE_FRACTIONS[m.group(2)])
    try:
        parts = token.split()
        if len(parts) == 2 and "/" in parts[1]:  # "1 1/2"
            return float(parts[0]) + float(Fraction(parts[1]))
        if "/" in token:  # "1/2"
            return float(Fraction(token))
        return float(token)
    except (ValueError, ZeroDivisionError):
        return total or None


_NUM = (r"\d+\s*[" + "".join(_UNICODE_FRACTIONS) + r"]"      # "1½" / "1 ½"
        r"|\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+"
        r"|[" + "".join(_UNICODE_FRACTIONS) + r"]")
_QTY_RE = re.compile(
    r"^\s*(?P<qty>" + _NUM + r")"
    # Optional range ("2-3", "2 to 3"). The low end stays authoritative for the
    # structured quantity; the high end is captured so display text can keep it
    # instead of silently dropping the "-3".
    r"(?:\s*(?:-|–|to)\s*(?P<qty_hi>" + _NUM + r"))?"
    r"\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

# One leading bullet or numbered marker, as pasted straight out of a blog or a
# YouTube description.
#
# Unambiguous bullet glyphs may hug the text ("•2 cups"). Characters that carry
# meaning inside an ingredient line must be separated by whitespace:
#   * "*2 tbsp cornstarch" is a FOOTNOTE reference, and stripping it orphans the
#     footnote — only "* 2 tbsp" is a bullet.
#   - a hyphen/dash can begin a compound or a negative, so likewise.
#   N. must be followed by space, or "1.5 cups" reads as list item 1.
_LIST_MARKER_RE = re.compile(r"^\s*(?:[•▪▢‣◦·]\s*|[-–—*]\s+|\d+[.)]\s+)")


def strip_list_marker(s: str) -> str:
    """Drop one leading list marker. A bullet carries no information and, left
    in place, blocked the anchored quantity match and then landed in the food
    name ("- 2 cups flour" -> food "- 2 cups flour")."""
    return _LIST_MARKER_RE.sub("", s, count=1)


def _split_approximator(s: str) -> tuple[str, str]:
    """(approximator, remainder) — e.g. ("About", "2 cups milk").

    Returned rather than just discarded so a caller that REWRITES the line
    (scale_line) can put it back: a scaled amount is still approximate, and
    dropping the hedge made the new line read more precise than it is.
    """
    low = s.lower()
    for word in _APPROXIMATORS:
        if low.startswith(word + " "):
            return s[:len(word)], s[len(word):].lstrip()
    return "", s


def _strip_approximator(s: str) -> str:
    return _split_approximator(s)[1]


def _match_unit(rest: str):
    """(canonical_unit, remaining_text) for a unit at the start of `rest`.

    Handles a leading bracketed pack size and a trailing period on an
    abbreviation ("2 c. sugar"), both of which are everywhere in scraped
    recipes and both of which previously stopped the unit being recognised.
    """
    skipped = _LEADING_PAREN_RE.match(rest)
    if skipped:
        # Keep the bracket text in the food, but look past it for the unit.
        after = rest[skipped.end():]
        unit, remainder = _match_unit(after)
        if unit:
            return unit, (rest[:skipped.end()] + remainder).strip()
    low = rest.lower()
    for key in _UNIT_KEYS:
        for token in (key, key + "."):
            if low == token or low.startswith(token + " "):
                return canonical_unit(key), rest[len(token):].strip()
    return None, rest


# --- Things buried in the food name -----------------------------------------
# A real pasted line: "1 gram Garlic Powder or ¹/₂ teaspoon (1 g)". Parsed
# naively the food name comes out as "Garlic Powder or ¹/₂ teaspoon (1 g)" —
# which contains a DUPLICATE of its own amount, an ALTERNATIVE way to measure
# it, and (on the next line of the same recipe) the word "optional". All three
# then travel as part of the name: into the ingredient column, into shopping,
# and into the shared Food catalog, where "Italian Seasoning optional (1 g)"
# becomes a food you can pick from an autocomplete forever.
#
# Each is pulled out of the name and returned separately, so the name is clean
# and nothing is lost. They are DERIVED, never stored: the alternative is a
# restatement of the amount we already parsed, so a column would be a second
# truth that scaling has to keep in sync — and scaling it is just arithmetic on
# the alternative's own quantity, which needs no stored state. The text in
# `display` stays the single source of truth; scale_line rewrites it whole,
# alternative included, and DROPS a restatement that has gone stale (keeping
# "(1 g)" beside a scaled "3 g" would be actively wrong).
_OPTIONAL_PAREN_RE = re.compile(r"\s*\(\s*optional\s*\)\s*", re.IGNORECASE)
_OPTIONAL_TAIL_RE = re.compile(r"(?:\s*[,;]\s*|\s+)optional\s*$", re.IGNORECASE)
_TRAILING_PAREN_RE = re.compile(r"\s*\(([^()]*)\)\s*$")
_OR_RE = re.compile(r"\s+or\s+", re.IGNORECASE)


def _base_amount(qty: float | None, unit: str | None):
    """(dimension, amount in the dimension's base unit), or (None, None)."""
    if qty is None or not unit:
        return None, None
    if unit in _WEIGHT_G:
        return "weight", qty * _WEIGHT_G[unit]
    if unit in _VOLUME_ML:
        return "volume", qty * _VOLUME_ML[unit]
    return None, None


def _render_measure(qty: float | None, unit: str | None) -> str:
    if qty is None:
        return ""
    return f"{format_qty_for_unit(qty, unit)} " \
           f"{pluralize_unit(unit, round(qty, 2))}".strip()


def _restates(inner: dict, qty: float | None, unit: str | None) -> bool:
    """Whether a bracketed amount is the SAME measure as the line's own.

    Compared in base units and within a whisker, so "(1 g)" beside "1 gram" and
    "(8.8 oz)" beside "250 g" are both recognised however they were spelled.
    Deliberately refuses to compare ACROSS dimensions: "2 cups flour (250 g)" is
    a genuine weight annotation a cook wants to keep, and converting it would
    need a density — a guess this has no business making while it decides
    whether to delete someone's text.
    """
    dim_a, base_a = _base_amount(inner["qty"], inner["unit"])
    dim_b, base_b = _base_amount(qty, unit)
    if dim_a is None or dim_a != dim_b or not base_b:
        return False
    return abs(base_a - base_b) <= 0.02 * base_b


def _split_measure_parts(rest: str, qty: float | None, unit: str | None):
    """``(food, alternative, optional)`` for an ingredient's food text."""
    alt = ""
    optional = bool(_OPTIONAL_PAREN_RE.search(rest))
    food = _OPTIONAL_PAREN_RE.sub(" ", rest).strip()

    # The bare word "optional" and a restated bracket are both anchored at the
    # END, so either one HIDES the other and which one is outermost is up to the
    # author: both "Seasoning optional (1 g)" and "Seasoning (1 g) optional"
    # occur. Peeling in a fixed order fixed one spelling and left the other
    # alone, so this peels repeatedly until nothing more comes off. Bounded
    # because every pass that does anything makes the string strictly shorter.
    for _ in range(4):
        peeled = False
        if _OPTIONAL_TAIL_RE.search(food):
            optional, peeled = True, True
            food = _OPTIONAL_TAIL_RE.sub("", food)
        # A trailing bracket holding NOTHING BUT the line's own amount again.
        # In the SAME unit it is a pure duplicate of the first column and is the
        # one piece deleted rather than reported — after scaling it would not
        # merely be stale, it would contradict the number beside it. In a
        # different unit of the same dimension it is a conversion ("1 lb ground
        # beef (450 g)"), which is the one thing a reader of the other
        # measurement system needs, so it is kept as an alternative instead.
        m = _TRAILING_PAREN_RE.search(food)
        if m:
            inner = parse_line(m.group(1))
            if inner["qty"] is not None and inner["unit"] and not inner["rest"] \
                    and _restates(inner, qty, unit):
                if inner["unit"] != unit:
                    alt = m.group(1).strip()
                food = food[:m.start()].rstrip()
                peeled = True
        if not peeled:
            break

    # "X or <amount>" — an alternative WAY TO MEASURE the same thing, which is
    # what a cook without a scale reads. Accepted only when the text after "or"
    # is an amount and nothing else: "milk or 240 ml water" names a different
    # FOOD (a substitution, not a measure) and "salt or pepper to taste" names
    # no amount at all, and silently restructuring either would be a guess.
    last = None
    for found in _OR_RE.finditer(food):
        last = found
    if last:
        after = parse_line(food[last.end():])
        if after["qty"] is not None and after["unit"] and not after["rest"]:
            # The author's OWN words, not a re-rendering of them. On an unscaled
            # recipe nothing has moved, so showing "or 1/2 tsp" where they typed
            # "or ½ teaspoon" rewrites text for no reason. scale_line re-renders
            # it only when it actually scales the number.
            alt = food[last.end():].strip()
            food = food[:last.start()].rstrip()

    return food.strip(" ,;"), alt, optional


def parse_line(text: str) -> dict:
    """Parse a free-text ingredient line into
    {qty, unit(canonical), rest, range_hi, prefix, alt, optional}. Any part may
    be None/''/False when it isn't present. Never raises.

    ``range_hi`` is the high end of "2-3 cloves" — the low end remains the
    authoritative structured quantity, so every existing consumer is unaffected;
    it exists so display text can keep the range instead of losing it.

    ``prefix`` is leading text that sat BEFORE the quantity and was lifted out to
    reach it (an approximator, an "(optional)" bracket). A caller that rewrites
    the line puts it back at the FRONT; appending it to ``rest`` instead moved it
    into the middle of the ingredient ("2 tbsp (optional) chili flakes").

    ``rest`` is the FOOD, with an alternative measure, a bracket that merely
    restated the amount, and the word "optional" taken out of it and reported in
    ``alt``/``optional`` instead (see the block above _split_measure_parts).
    Every consumer wanted the clean name — the density lookup, shopping's
    grouping, the Food catalog — and the pieces are returned rather than
    discarded, so scale_line can put them back.

    That cleaning happens ONLY when the line has a leading amount to carry. A
    line with no parseable quantity comes back exactly as written: it renders as
    one unstructured string, so there is nowhere for a lifted-out part to go,
    and stripping a word from text we are showing whole would simply lose it.
    """
    raw = strip_list_marker((text or "").strip())
    approx, body = _split_approximator(raw)
    # Match against a numeral-normalised COPY so "¹/₂ tsp" parses, but keep
    # `body` for slicing the food text out of. The normalisation is
    # length-preserving, so an offset into one is the same offset into the other
    # — which is what lets the glyphs the author actually typed survive into the
    # food name while the quantity is still read correctly.
    body_n = _normalize_numerals(body)
    m = _QTY_RE.match(body_n)
    if not m or not m.group("qty"):
        # A leading "(optional)" / "(or more to taste)" bracket defeats the
        # anchored quantity match. Look past ONE such group; if the remainder
        # carries a quantity, keep the bracket as a prefix.
        lead = _LEADING_PAREN_RE.match(body)
        if lead:
            inner = parse_line(body[lead.end():])
            if inner["qty"] is not None:
                bracket = body[:lead.end()].strip()
                inner["prefix"] = " ".join(
                    p for p in (approx, bracket, inner["prefix"]) if p)
                # The bracket is where "(optional)" ends up on this path, so the
                # flag has to be read from it too — otherwise the same line
                # reports differently depending on which end the author put the
                # word, which is exactly the inconsistency a flag exists to end.
                inner["optional"] = inner["optional"] or bool(
                    _OPTIONAL_PAREN_RE.search(bracket))
                return inner
        # Nothing was lifted out, so nothing needs restoring: return the line
        # with only the bullet gone. Stripping the approximator here would drop
        # "About" with no prefix to carry it back.
        # `optional` is still REPORTED for a line like "Salt, optional" — a
        # caller may want to badge it — but the text is handed back whole.
        return {"qty": None, "unit": None, "rest": raw, "range_hi": None,
                "prefix": "", "alt": "",
                "optional": bool(_OPTIONAL_PAREN_RE.search(raw)
                                 or _OPTIONAL_TAIL_RE.search(raw))}
    qty = _parse_number(m.group("qty"))
    unit, rest = _match_unit(body[m.start("rest"):].strip())
    hi = _parse_number(m.group("qty_hi")) if m.group("qty_hi") else None
    food, alt, optional = _split_measure_parts(rest, qty, unit)
    return {"qty": qty, "unit": unit, "rest": food, "range_hi": hi,
            "prefix": approx, "alt": alt, "optional": optional}


# Canonical unit -> plural spelling, for rendering a quantity back as text a
# cook would actually write. canonical_unit() deliberately collapses every
# spelling to ONE singular key ("cups" -> "cup"), which is right for arithmetic
# and wrong for display the moment a quantity moves: "4 cup flour".
#
# Abbreviations (tsp, tbsp, g, kg, ml, l, oz, lb, fl oz) are deliberately
# ABSENT — "4 tsps" is not how anyone writes it — so anything not listed here
# is returned unchanged. Only whole words pluralize.
#
# Every metric and imperial MEASURE now canonicalises to its abbreviation (see
# the table at the top), so "gram"/"ounce"/"fluid ounce" entries that used to
# live here are unreachable and were removed rather than left as decoration.
# This is also what makes one recipe render one way: a line written "1 gram" and
# a line written "40 grams" both come out in 'g'.
_PLURALS = {
    # volume
    "cup": "cups", "pint": "pints", "quart": "quarts", "gallon": "gallons",
    # counts (all of _COUNT_UNITS; -es where the singular ends in ch/sh/s/x/z)
    "pinch": "pinches", "dash": "dashes", "handful": "handfuls",
    "clove": "cloves", "slice": "slices", "can": "cans", "tin": "tins",
    "package": "packages", "stick": "sticks", "sprig": "sprigs",
    "bunch": "bunches", "head": "heads", "jar": "jars", "bottle": "bottles",
    "packet": "packets",
}


def pluralize_unit(unit: str | None, qty: float | None) -> str:
    """Render a canonical unit for display beside ``qty``.

    Plural only above 1. Recipe English keeps the singular for fractions —
    "1/2 cup flour", "3/4 teaspoon salt" — so pluralizing on "anything but
    exactly 1" would be wrong in the direction people notice most. Whole words
    pluralize; abbreviations are never touched. Never raises — an unknown unit
    is returned as given.
    """
    u = (unit or "").strip()
    if not u or qty is None:
        return u
    # Canonicalise first. Callers pass parse_line's already-canonical unit, but
    # this is public and a caller holding the raw word ("grams") must get the
    # same answer as one holding the canonical — the kind of split that made a
    # single recipe render two ways.
    u = canonical_unit(u) or u
    if qty <= 1:
        return u
    return _PLURALS.get(u, u)


def format_qty(value: float) -> str:
    """Render a quantity for display: nearest common fraction for small values,
    otherwise a tidy decimal."""
    if value is None:
        return ""
    if value <= 0:
        return "0"
    frac = Fraction(value).limit_denominator(8)
    if abs(float(frac) - value) < 0.02 and frac.denominator != 1:
        whole, rem = divmod(frac, 1)
        return (f"{int(whole)} {rem}" if whole else str(rem))
    rounded = round(value, 2)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}"


# Metric units are written as decimals, never as vulgar fractions. "1 1/2 cups"
# is how recipes are written; "337 1/2 g" is not — nobody weighs half a gram of
# mushrooms, and the fraction reads as precision the scaling never had.
_DECIMAL_UNITS = {
    # whole units only: a fraction of a gram or millilitre is noise
    "g": 0,
    "ml": 0,
    # a fraction of a kilo or litre is real, but as a decimal — 1.19 kg
    "kg": 2,
    "l": 2,
}


def format_qty_for_unit(value: float, unit: str | None) -> str:
    """Render a quantity for display beside ``unit``.

    Defers to :func:`format_qty` — fractions and all — for everything except the
    metric units, where a fraction is simply the wrong notation. Sub-1 metric
    amounts keep one decimal rather than rounding away to "0", matching what
    _format_grams already does on the weight-annotation path.
    """
    # Canonicalise, for the same reason as pluralize_unit: _DECIMAL_UNITS is
    # keyed on 'g'/'ml', so a caller holding "gram" used to slip past it and get
    # "337 1/2" — the half-gram fraction this table exists to prevent.
    places = _DECIMAL_UNITS.get(canonical_unit(unit) or unit)
    if places is None or value is None or value <= 0:
        return format_qty(value)
    if places == 0 and value < 1:
        return f"{round(value, 1):g}"
    return f"{round(value, places):g}"


# --- Rounding a scaled COUNT -------------------------------------------------
# "3 large eggs" x1.5 came out as "4 1/2 large eggs". You cannot buy half an
# egg, and nobody has ever acted on that line.
#
# The policy: a count is rounded to the coarsest increment the ingredient can
# actually be USED in, then nudged in whichever direction fails safe. BOTH
# halves are needed, and that is why one global rule cannot work — rounding up
# is right for aromatics and wrong for expensive protein; a half is legitimate
# for an onion and meaningless for an egg. Four tiers:
#
#   WHOLE_UP    increment 1, ties UP — indivisible things bought as units: eggs,
#       cans, jars, packets, slices, bunches. A half has no meaning, and
#       under-buying is the worse error (you cannot use the half you don't
#       have). "1 can" x0.5 -> "1 can" is correct: you buy the whole can.
#   HALVES      increment 0.5, ties up — produce a cook cuts: onion, pepper,
#       lemon, apple, courgette, a stick of butter. "1 1/2 onions" is a line
#       people write and act on, so forcing a whole number here would be the
#       opposite error.
#   UP          increment 1, always ceil — cheap, forgiving, hard to overdo:
#       garlic cloves, pinches, sprigs, herbs, spices. More garlic has never
#       ruined a dish; under-seasoning routinely does.
#   WHOLE_DOWN  increment 1, ties DOWN — expensive protein counted by the piece:
#       chicken breasts, salmon fillets. These dominate the bill, so thrift is
#       the safe direction: a cook who wants more adds one, whereas a surplus
#       fillet is waste the app chose on their behalf.
#
# Nothing ever rounds to zero. A line that asked for an ingredient still asks
# for it at x0.25, so the result floors at one increment.
#
# The tier comes from the FOOD CATALOG — food_resolve's SEED_FOODS already
# classifies egg / vegetable / fruit / meat / fish / herb / spice for the
# allergen guard — rather than from a second hardcoded list here that would
# drift from it. An unrecognised food falls to WHOLE_UP, which is the safe
# default for a bare count: "0.37 tortillas" is never an answer.
_WHOLE_UP, _WHOLE_DOWN, _HALVES, _UP = "whole_up", "whole_down", "halves", "up"

# A count UNIT beats the food, because the unit names the discrete thing being
# counted: "2 cloves garlic" counts cloves, not garlic bulbs.
_COUNT_UNIT_TIERS = {
    "can": _WHOLE_UP, "tin": _WHOLE_UP, "jar": _WHOLE_UP, "bottle": _WHOLE_UP,
    "packet": _WHOLE_UP, "package": _WHOLE_UP, "slice": _WHOLE_UP,
    "bunch": _WHOLE_UP, "head": _WHOLE_UP,
    "stick": _HALVES,
    "clove": _UP, "pinch": _UP, "dash": _UP, "handful": _UP, "sprig": _UP,
}
_FOOD_CLASS_TIERS = {
    "egg": _WHOLE_UP,
    "vegetable": _HALVES, "fruit": _HALVES,
    "herb": _UP, "spice": _UP, "seasoning": _UP,
    "meat": _WHOLE_DOWN, "fish": _WHOLE_DOWN, "shellfish": _WHOLE_DOWN,
    "protein": _WHOLE_DOWN,
}

# Guards the float ties. 4.5 is exact in binary, but 0.1*45 is not, and a tie
# that lands a hair below its boundary rounds the wrong way — which is the one
# thing a reader would notice ("x1.5 of 3 gives 4, x1.5 of 3.0 gives 5").
_TIE = 1e-9


def _count_tier(unit: str | None, food_text: str) -> str | None:
    """Which rounding tier a count amount falls in, or None when the amount
    isn't a count at all (grams, millilitres, cups — those keep their decimals
    and fractions, which are meaningful).

    ⚠️ "no unit" is NOT the same as "a count", and conflating them rounded real
    measures into nonsense. An unrecognised unit leaves ``unit`` None with the
    word still sitting at the front of ``food_text`` — so "2 dl cream" x1.25
    came out as "3 dl cream", a 20% error silently introduced by a rounder that
    thought it was counting creams. Every unit this module does not know (dl,
    cl, inch, sheet, rasher, and every non-English spelling) took that path.

    So a bare count only rounds when the CATALOG recognises the food and says
    what kind of thing it is. An unrecognised food is left exactly alone: not
    rounding "3 tortillas" x1.5 to a whole number is a cosmetic miss, while
    rounding "2 dl cream" is a wrong recipe, and we cannot tell those apart
    without knowing the food.

    And the number must be counting the FOOD. "3 rashers bacon" resolves to
    bacon, but the count is of rashers — rounding it as meat took 1.5 rashers
    down to 1. food_resolve.counted_portion_word spots that case (the giveaway
    is a PLURAL qualifier, which an adjective like "red" never is), and a count
    of something the lexicon has no unit for is left alone.
    """
    if unit:
        # Explicit default: a count unit added to _COUNT_UNITS without a tier
        # must fall back to the safe discrete one, not silently start
        # classifying by its food instead.
        return _COUNT_UNIT_TIERS.get(unit, _WHOLE_UP) if unit in _COUNT_UNITS else None
    from . import food_resolve  # local: keeps this module import-light

    if food_resolve.counted_portion_word(food_text):
        return None
    return _FOOD_CLASS_TIERS.get(food_resolve.classification(food_text))


def round_count(value: float, unit: str | None, food_text: str = "") -> float:
    """Round a COUNT to something a cook can act on — see the policy above.

    Returns ``value`` unchanged for any real measure (g, ml, cup, tsp), where a
    fraction is information rather than noise, and for any food the catalog does
    not recognise (see _count_tier). Pure and idempotent, and never returns 0
    for a positive input. A non-finite value is the caller's bug and is left to
    raise, exactly as format_qty already does.
    """
    if value is None or value <= 0:
        return value
    tier = _count_tier(unit, food_text)
    if tier is None:
        return value
    if tier == _HALVES:
        return max(math.floor(value * 2 + 0.5 + _TIE) / 2, 0.5)
    if tier == _UP:
        return float(max(math.ceil(value - _TIE), 1))
    if tier == _WHOLE_DOWN:
        return float(max(math.ceil(value - 0.5 - _TIE), 1))
    return float(max(math.floor(value + 0.5 + _TIE), 1))


# Classifications whose foods are COUNTABLE — things you buy and count as
# individual objects. Only these may have their noun made to agree with a scaled
# count, and the restriction is the whole safety story: "chicken", "salmon",
# "beef", "rice" and "butter" are mass nouns in recipe English, so a general
# rule would produce "2 chickens" and "2 salmons". Vegetables, fruit and eggs
# are exactly the foods a bare count is ever written against.
_COUNTABLE_CLASSES = frozenset({"vegetable", "fruit", "egg"})

# ...and the classification alone is NOT enough, because grammatical number is
# not something the catalog records. These are classified vegetable/fruit (the
# catalog is right — they are) but are MASS nouns in recipe English: you write
# "1 inch ginger", never "3 inch gingers", and "a bulb of garlic", never
# "2 garlics". Both of those were produced before this list existed.
#
# This is a denylist rather than a classification because it is a fact about
# English, not about food, and it belongs with the code that conjugates rather
# than with the lexicon that identifies. Unknown words are already left alone,
# so the cost of a missing entry here is bounded to foods the catalog knows.
_MASS_NOUNS = frozenset({
    "garlic", "ginger", "celery", "lettuce", "spinach", "sweet corn", "corn",
})


def _pluralize_noun(word: str) -> str:
    """The plural of a food noun — the inverse of food_resolve.singularize, so a
    name that folds one way comes back the other ("tomato" -> "tomatoes", not
    "tomatos")."""
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if len(word) > 1 and word.endswith("y") and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if len(word) > 1 and word.endswith("o") and word[-2] not in "aeiou":
        return word + "es"
    return word + "s"


def agree_food_noun(food_text: str, qty: float | None) -> str:
    """Make a counted food's noun agree with ``qty`` — "1 1/2 onions", not
    "1 1/2 onion"; "1 egg", not "1 eggs".

    Only for a BARE count. When a unit is present the unit carries the number
    ("3 cloves garlic") and pluralizing the food as well would give "3 cloves
    garlics", so callers must not use this there.

    Conservative by construction: the word is only touched when the food
    catalog recognises it, classifies it as countable, and it is not one of the
    mass nouns the classification cannot distinguish (see _MASS_NOUNS). So an
    unrecognised name ("bone-in chicken thighs"), "1 inch ginger" and "1 bulb
    garlic" all come back untouched. Rewriting food text we do not understand is
    how names get mangled.
    """
    if not food_text or qty is None:
        return food_text
    from . import food_resolve  # local: keeps this module import-light

    # The head noun is the last word before any preparation clause: the comma in
    # "onion, diced" means the rest is not part of the name.
    head, sep, tail = food_text.partition(",")
    words = head.split()
    if not words:
        return food_text
    singular = food_resolve.singularize(words[-1])
    if singular.lower() in _MASS_NOUNS:
        return food_text
    if food_resolve.classification(singular) not in _COUNTABLE_CLASSES:
        return food_text
    words[-1] = _pluralize_noun(singular) if qty > 1 else singular
    return " ".join(words) + sep + tail


def annotations(display: str) -> dict:
    """The row-level facts a two-column view renders BESIDE the food text:
    ``{"alt", "optional", "unmeasured"}``.

    One function so the serializer and the scaled view cannot disagree — they
    derived this separately once, which is how two reads of one line start
    saying different things.

    ``optional`` is reported only when the word was actually LIFTED OUT of the
    text the view renders. parse_line also reports it for a line it hands back
    whole ("Salt, optional", "(optional) 2 tbsp chili flakes"), and rendering a
    flag beside text that still contains the word produced "Salt, optional ·
    optional" — a duplicate created by the very flag meant to end a duplicate.

    ``unmeasured`` is "no actionable amount anywhere", NOT "the amount column is
    empty". A line whose amount sits behind leading text ("About 2 cups milk")
    also has an empty amount cell, and must not be labelled as having no amount.
    """
    p = parse_line(display or "")
    _, _, rest = split_amount(display, None)
    return {
        "alt": p["alt"],
        "optional": p["optional"] and "optional" not in rest.lower(),
        "unmeasured": p["qty"] is None,
    }


def split_amount(display: str, quantity: float | None = None) -> tuple[str, str, str]:
    """Split an ingredient line into ``(amount, unit, rest)`` for a two-column
    display.

    Accounts for every word of ``display`` between these three parts and the two
    that parse_line reports alongside them — the alternative measure and the
    ``optional`` flag. A caller that renders the three and drops those two DOES
    lose text, so ingredient_out ships them as ``altText``/``optional`` and the
    recipe view renders both. Nothing is dropped silently; the restated amount
    is the one deliberate deletion, because it duplicates the amount already in
    the first column.

    ONE decision drives both halves, deliberately. Deriving the amount from the
    stored ``quantity`` while stripping it from the text independently meant a row
    holding ``quantity=0`` next to a numeric display ("2 cups flour" — exactly
    what the AI structuring path writes, and what a legacy NULL looks like) had
    its amount removed from the text and blanked in the column: the user's own
    number disappeared from the page.
    """
    display = display or ""
    p = parse_line(display)
    if p["qty"] is None or p["prefix"]:
        # No leading amount to lift out — or text sits IN FRONT of it ("About 2
        # cups milk", "(optional) 1 tbsp x"). A prefix can't go in a numeric
        # column and can't move behind the amount without garbling the line
        # ("2 cups About milk"), so leave the whole line as one string. It reads
        # as an unstructured row, which is honest, rather than reordered.
        return "", "", display
    # There IS a leading amount, and `rest` has had it removed, so it MUST be
    # rendered. Prefer the structured quantity (it's what scaling updates) but
    # fall back to the parsed one rather than showing nothing.
    qty = quantity if quantity else p["qty"]
    # The unit comes from the TEXT, not the row: `rest` had exactly that word
    # removed, so echoing anything else would inject or drop a word.
    return (format_qty_for_unit(qty, p["unit"]),
            pluralize_unit(p["unit"], round(qty, 2)), p["rest"])


def scale_line(text: str, factor: float) -> str:
    """Return the ingredient line with its leading quantity multiplied by
    ``factor``. Lines with no parseable quantity are returned unchanged."""
    if not factor or factor == 1:
        return text
    parsed = parse_line(text)
    if parsed["qty"] is None:
        return text
    # A count is rounded to an amount a cook can act on; a real measure keeps
    # its arithmetic. See the policy block above round_count — this is the only
    # place scaling is allowed to change a number for reasons other than the
    # factor, and it is deliberately NOT applied to an unscaled line: a cook who
    # typed "1.5 eggs" meant it, and rewriting what they typed is a different
    # (and unasked-for) behaviour from rounding what WE computed.
    scaled = round_count(parsed["qty"] * factor, parsed["unit"], parsed["rest"])
    new_qty = format_qty_for_unit(scaled, parsed["unit"])
    # Scale BOTH ends of a range. Only the display text grows a range here — the
    # structured quantity stays the low end (see parse_line), so shopping
    # consolidation, weight conversion and the MCP/HA surfaces are untouched.
    # Previously "2-3 cloves" doubled to "4 cloves": the "-3" was consumed by the
    # regex and silently destroyed.
    hi = parsed.get("range_hi")
    if hi is not None:
        hi = round_count(hi * factor, parsed["unit"], parsed["rest"])
        new_qty = f"{new_qty}-{format_qty_for_unit(hi, parsed['unit'])}"
    # Pluralize on the value we actually RENDER, not the raw product: format_qty
    # rounds, so 1.0003 prints "1" and an exact comparison would print "1 cups".
    # A range is plural whenever its high end is.
    shown = round(hi if hi is not None else scaled, 2)
    unit_text = pluralize_unit(parsed["unit"], shown)
    # With no unit the FOOD carries the number, so it has to agree with the new
    # one: "1 onion" x1.5 read "1 1/2 onion". With a unit the unit already
    # carries it ("3 cloves garlic"), so the food is left alone.
    rest = parsed["rest"] if parsed["unit"] else agree_food_noun(parsed["rest"], shown)
    # Put back what parse_line lifted out of the food name, so the rewritten
    # line still says everything the author's did. The alternative is SCALED,
    # not copied: "1 g garlic powder or 1/2 tsp" tripled has to read "or 1 1/2
    # tsp", and a stale alternative beside a scaled amount is worse than none.
    # A restatement is deliberately NOT put back — "(1 g)" beside "3 g" is not
    # stale text, it is a contradiction.
    alt = parsed.get("alt") or ""
    if alt:
        ap = parse_line(alt)
        if ap["qty"] is not None:
            alt = _render_measure(
                round_count(ap["qty"] * factor, ap["unit"], parsed["rest"]),
                ap["unit"])
    tail = " ".join(p for p in (unit_text, rest) if p)
    if alt:
        tail = f"{tail} or {alt}"
    # ...unless the word is already coming back at the front in `prefix`. The
    # author wrote "(optional) 1 tbsp chili flakes" and must not get it twice.
    if parsed.get("optional") \
            and not _OPTIONAL_PAREN_RE.search(parsed.get("prefix") or ""):
        tail = f"{tail}, optional"
    # Restore whatever sat in front of the quantity — "About" (the amount is
    # still approximate after scaling) or a leading "(optional)" bracket. Goes
    # back at the FRONT, where the user wrote it.
    head = f"{parsed['prefix']} " if parsed.get("prefix") else ""
    return f"{head}{new_qty} {tail}".strip()


_CANONICAL_DENSITIES: dict[str, float] | None = None


def _canonical_densities() -> dict[str, float]:
    """The density table re-keyed by canonical food name.

    The table is hand-written with everyday spellings ("yogurt"), but lookups
    arrive canonicalised ("yoghurt"), so a key could sit in the table and never
    be reachable. Re-keying once removes a whole class of silent miss instead of
    asking whoever adds a row to know the canonical spelling.

    Built lazily and cached: food_resolve is imported locally to keep this
    module import-light, so this cannot be a module-level constant.
    """
    global _CANONICAL_DENSITIES
    if _CANONICAL_DENSITIES is None:
        from . import food_resolve
        table = {}
        for name, density in _DENSITY_G_PER_ML.items():
            table[name] = density
            key = food_resolve.match_key(name)
            if key:
                table.setdefault(key, density)
        _CANONICAL_DENSITIES = table
    return _CANONICAL_DENSITIES


def _density_for(text: str) -> float | None:
    """Density for an ingredient's food text, or None when it isn't known.

    This used to take the longest density-table key appearing ANYWHERE in the
    text, which reads plausibly and is wrong: "rice vinegar" contains "rice", so
    a cup of rice vinegar was weighed at rice's 0.85 g/ml, and peanut butter got
    butter's 0.96. A substring is not evidence of being the same substance.

    So the text is canonicalised first and matched as a whole. "unsalted butter"
    still finds butter (preparation words come off in ``match_key``), while
    "peanut butter" canonicalises to itself and simply isn't in the table.

    Returning None is the correct failure: the weight view omits an estimate it
    cannot make, rather than showing a confidently wrong number.
    """
    from . import food_resolve  # local: keeps this module import-light

    canonical = food_resolve.match_key(text)
    if not canonical:
        return None

    table = _canonical_densities()
    if canonical in table:
        return table[canonical]

    # Fall back to the head noun, so "sesame oil" and "maple syrup" keep the
    # density they have always had — but only when the qualifier is not itself a
    # materially different food. That is the one check that separates "sesame
    # oil is oil" from "peanut butter is not butter"; both look identical to a
    # substring search, which is why the old rule got the second one wrong.
    for head in sorted(table, key=len, reverse=True):
        if not canonical.endswith(" " + head):
            continue
        qualifier = canonical[: -(len(head) + 1)].strip()
        if food_resolve._differs_materially(qualifier, head):
            return None
        return table[head]
    return None


def _reference_grams(unit: str, food_text: str) -> float | None:
    """Grams for ONE `unit` of this food from the shared foodcore dataset.

    The whole foodcore dependency lives behind this one call (see
    ``food_reference``), imported locally so this module stays import-light and
    so the seam is removable. It returns None — not an exception, not a zero —
    for every unjustified answer, including the shipped default of no dataset
    configured at all.
    """
    from . import food_reference  # local: keeps this module import-light

    return food_reference.grams_per(unit, food_text)


def to_grams(text: str, learned=None) -> float | None:
    """Best-effort grams for an ingredient line: direct for weight units, and
    for volume units when a reference weight or the food's density is known.
    None otherwise.

    ``learned`` is an optional ``(unit, food) -> grams_per_unit | None`` callable
    supplying remembered conversions for units this module has no physics for —
    a stick of butter, a can of tomatoes. It is a parameter rather than an import
    so this module stays pure and database-free, and so a caller on a hot read
    path can simply not pass one.

    It is consulted **last**. A shipped weight or density always wins: a value
    looked up on the internet must never be able to override a known one.
    """
    p = parse_line(text)
    if p["qty"] is None or not p["unit"]:
        return None
    if p["unit"] in _WEIGHT_G:
        return p["qty"] * _WEIGHT_G[p["unit"]]
    if p["unit"] in _VOLUME_ML:
        # The reference dataset first: a MEASURED USDA portion weight beats a
        # hand-written density, and importing reference data only to override
        # it with folklore would defeat the point. It answers None unless an
        # operator has imported a dataset, so the default path below is
        # unchanged and so is every number myMeal has ever printed.
        reference = _reference_grams(p["unit"], p["rest"])
        if reference:
            return p["qty"] * reference
        # Then the built-in table. This fallback is load-bearing, not
        # belt-and-braces: foodcore returns Unresolved wherever it cannot
        # justify an answer (no such food, no portion row, ambiguous
        # candidates), and a refusal must not withdraw a conversion myMeal has
        # shipped for years — scaling and the shopping list both read it.
        density = _density_for(p["rest"])
        if density:
            return p["qty"] * _VOLUME_ML[p["unit"]] * density
        # A volume unit with an unknown density can still fall through to a
        # learned value ("1 cup of pitted dates"), so no early return here.
    if learned:
        try:
            grams = learned(p["unit"], p["rest"])
        except Exception:  # noqa: BLE001 - a lookup must never break a render
            return None
        if grams and grams > 0:
            return p["qty"] * grams
    return None


def _format_grams(grams: float) -> str:
    return f"{round(grams)} g" if grams >= 1 else f"{round(grams, 1)} g"


def to_weight_line(text: str, learned=None) -> str:
    """Rewrite an ingredient line to a weight measurement when possible
    ("1 cup flour" → "125 g flour"); otherwise return it unchanged."""
    grams = to_grams(text, learned=learned)
    if grams is None:
        return text
    rest = parse_line(text)["rest"]
    return f"{_format_grams(grams)} {rest}".strip()


def annotate_weight(text: str, learned=None) -> str:
    """Append the weight in parentheses, KEEPING the original measure, when a
    conversion is possible ("1 cup flour" → "1 cup flour (125 g)"). Returns the
    line unchanged when there's no known density/weight. Non-destructive: this is
    what the recipe view's weight toggle shows."""
    grams = to_grams(text, learned=learned)
    if grams is None:
        return text
    return f"{text.strip()} ({_format_grams(grams)})"
