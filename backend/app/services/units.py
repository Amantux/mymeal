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
}

# Approximate densities (grams per millilitre) for common pantry foods, matched
# by keyword against the ingredient text. Enough for a sensible weight readout;
# not laboratory-grade. Only used when a volume needs to become a weight.
_DENSITY_G_PER_ML = {
    "water": 1.0, "milk": 1.03, "cream": 1.0, "stock": 1.0, "broth": 1.0,
    "oil": 0.92, "olive oil": 0.92, "butter": 0.96, "honey": 1.42, "syrup": 1.37,
    "flour": 0.53, "sugar": 0.85, "brown sugar": 0.90, "powdered sugar": 0.56,
    "salt": 1.22, "rice": 0.85, "cocoa": 0.52, "oats": 0.41,
    "yogurt": 1.03, "ketchup": 1.14,
}

_UNICODE_FRACTIONS = {
    "½": Fraction(1, 2), "⅓": Fraction(1, 3), "⅔": Fraction(2, 3),
    "¼": Fraction(1, 4), "¾": Fraction(3, 4), "⅕": Fraction(1, 5),
    "⅛": Fraction(1, 8), "⅜": Fraction(3, 8), "⅝": Fraction(5, 8), "⅞": Fraction(7, 8),
}

# Longest unit spellings first so "fl oz" matches before "oz".
_UNIT_KEYS = sorted(
    set(_VOLUME_ML) | set(_WEIGHT_G) | set(_ALIASES), key=len, reverse=True
)


def canonical_unit(raw: str) -> str | None:
    u = (raw or "").strip().lower().rstrip(".")
    if not u:
        return None
    u = _ALIASES.get(u, u)
    return u if u in _VOLUME_ML or u in _WEIGHT_G else None


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


_NUM = r"\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+|[" + "".join(_UNICODE_FRACTIONS) + r"]"
_QTY_RE = re.compile(
    r"^\s*(?P<qty>" + _NUM + r")"
    # Optional range ("2-3", "2 to 3") — we keep the low end and ignore the rest.
    r"(?:\s*(?:-|–|to)\s*(?:" + _NUM + r"))?"
    r"\s*(?P<rest>.*)$",
    re.IGNORECASE,
)


def parse_line(text: str) -> dict:
    """Parse a free-text ingredient line into {qty, unit(canonical), rest}.
    Any part may be None/'' when it isn't present. Never raises."""
    s = (text or "").strip()
    m = _QTY_RE.match(s)
    if not m or not m.group("qty"):
        return {"qty": None, "unit": None, "rest": s}
    qty = _parse_number(m.group("qty"))
    rest = m.group("rest").strip()
    unit = None
    low = rest.lower()
    for key in _UNIT_KEYS:
        # unit token must be a whole word at the start of the remainder
        if low == key or low.startswith(key + " "):
            unit = canonical_unit(key)
            rest = rest[len(key):].strip()
            break
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


def _density_for(text: str) -> float | None:
    low = (text or "").lower()
    # Prefer the most specific (longest) matching food keyword.
    for food in sorted(_DENSITY_G_PER_ML, key=len, reverse=True):
        if food in low:
            return _DENSITY_G_PER_ML[food]
    return None


def to_grams(text: str) -> float | None:
    """Best-effort grams for an ingredient line: direct for weight units, and
    for volume units when the food's density is known. None otherwise."""
    p = parse_line(text)
    if p["qty"] is None or not p["unit"]:
        return None
    if p["unit"] in _WEIGHT_G:
        return p["qty"] * _WEIGHT_G[p["unit"]]
    if p["unit"] in _VOLUME_ML:
        density = _density_for(p["rest"])
        if density:
            return p["qty"] * _VOLUME_ML[p["unit"]] * density
    return None


def to_weight_line(text: str) -> str:
    """Rewrite an ingredient line to a weight measurement when possible
    ("1 cup flour" → "125 g flour"); otherwise return it unchanged."""
    grams = to_grams(text)
    if grams is None:
        return text
    rest = parse_line(text)["rest"]
    amount = f"{round(grams)} g" if grams >= 1 else f"{round(grams, 1)} g"
    return f"{amount} {rest}".strip()
