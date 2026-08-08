"""Measurement parsing, scaling, and volume↔weight conversion.

Recipe ingredient lines are free text ("1 1/2 cups flour"). This module parses
the leading quantity + unit so the app can (a) SCALE a recipe to a target
serving count and (b) where a food's density is known, present WEIGHT-based
measurements regardless of how the recipe was written ("1 cup flour" → "125 g
flour"), converting under the covers. Pure functions, no DB, never raises.
"""
from __future__ import annotations

import re
from fractions import Fraction

# --- Canonical units -> base amount. Volume base = millilitre, weight = gram --
_VOLUME_ML = {
    "ml": 1.0, "millilitre": 1.0, "milliliter": 1.0, "cc": 1.0,
    "l": 1000.0, "litre": 1000.0, "liter": 1000.0,
    "tsp": 4.92892, "teaspoon": 4.92892,
    "tbsp": 14.7868, "tablespoon": 14.7868,
    "fl oz": 29.5735, "fluid ounce": 29.5735,
    "cup": 236.588,
    "pint": 473.176, "quart": 946.353, "gallon": 3785.41,
}
_WEIGHT_G = {
    "g": 1.0, "gram": 1.0,
    "kg": 1000.0, "kilogram": 1000.0,
    "oz": 28.3495, "ounce": 28.3495,
    "lb": 453.592, "pound": 453.592,
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

# Map spellings/plurals/abbreviations to a canonical key above.
_ALIASES = {
    "milliliters": "ml", "millilitres": "ml", "millilitre": "ml",
    "liters": "l", "litres": "l", "litre": "l",
    "teaspoons": "tsp", "teaspoon": "tsp", "ts": "tsp",
    "tablespoons": "tbsp", "tablespoon": "tbsp", "tbs": "tbsp", "tbl": "tbsp",
    "cups": "cup", "c": "cup",
    "fluid ounces": "fl oz", "floz": "fl oz", "fl. oz.": "fl oz", "fl. oz": "fl oz",
    "pints": "pint", "pt": "pint",
    "quarts": "quart", "qt": "quart",
    "gallons": "gallon", "gal": "gallon",
    "grams": "g", "gr": "g", "gm": "g", "gramme": "g", "grammes": "g",
    "kilograms": "kg", "kilo": "kg", "kilos": "kg", "kgs": "kg",
    "ounces": "oz", "ozs": "oz",
    "pounds": "lb", "lbs": "lb", "#": "lb",
    # plurals of the count units
    "pinches": "pinch", "dashes": "dash", "handfuls": "handful",
    "cloves": "clove", "slices": "slice", "cans": "can", "tins": "tin",
    "packages": "package", "pkg": "package", "pkgs": "package",
    "sticks": "stick", "sprigs": "sprig", "bunches": "bunch", "heads": "head",
    "jars": "jar", "bottles": "bottle", "packets": "packet",
}

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
    # Optional range ("2-3", "2 to 3") — we keep the low end and ignore the rest.
    r"(?:\s*(?:-|–|to)\s*(?:" + _NUM + r"))?"
    r"\s*(?P<rest>.*)$",
    re.IGNORECASE,
)


def _strip_approximator(s: str) -> str:
    low = s.lower()
    for word in _APPROXIMATORS:
        if low.startswith(word + " "):
            return s[len(word):].lstrip()
    return s


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


def parse_line(text: str) -> dict:
    """Parse a free-text ingredient line into {qty, unit(canonical), rest}.
    Any part may be None/'' when it isn't present. Never raises."""
    s = _strip_approximator((text or "").strip())
    m = _QTY_RE.match(s)
    if not m or not m.group("qty"):
        return {"qty": None, "unit": None, "rest": s}
    qty = _parse_number(m.group("qty"))
    unit, rest = _match_unit(m.group("rest").strip())
    return {"qty": qty, "unit": unit, "rest": rest}


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


def scale_line(text: str, factor: float) -> str:
    """Return the ingredient line with its leading quantity multiplied by
    ``factor``. Lines with no parseable quantity are returned unchanged."""
    if not factor or factor == 1:
        return text
    parsed = parse_line(text)
    if parsed["qty"] is None:
        return text
    new_qty = format_qty(parsed["qty"] * factor)
    tail = " ".join(p for p in (parsed["unit"], parsed["rest"]) if p)
    return f"{new_qty} {tail}".strip()


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


def to_grams(text: str, learned=None) -> float | None:
    """Best-effort grams for an ingredient line: direct for weight units, and
    for volume units when the food's density is known. None otherwise.

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
