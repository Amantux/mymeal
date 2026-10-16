"""The unit and food-text vocabulary — ONE spelling table for the whole package.

Why this is its own module rather than living in the USDA adapter (where it was
first written): three layers need it, and they must not be stacked in the wrong
order.

* ``sources/usda.py`` normalises USDA's free-text modifiers while parsing.
* ``datasets.py`` normalises a measure on the way INTO the dataset file and on
  the way out of a lookup, so ``"liter"``, ``"litres"`` and ``"l"`` are the same
  row rather than three.
* ``convert.py`` normalises whatever a host app or a recipe line says.

If the table lived in the adapter, the storage layer would import an adapter —
a library importing its own plug-in — and a second provider adapter would have
nowhere to put the shared spellings but a copy. The estate already pays for
duplicated unit tables (myMeal's ``units.py``, Edibl's ``quantity.py``); the
whole point of foodcore is to have one.

Pure data and pure functions: no I/O, no clock, no state.
"""
from __future__ import annotations

import re

#: Volume units, in millilitres. US customary — FDC is a US dataset.
VOLUME_ML = {
    "ml": 1.0,
    "l": 1000.0,
    "tsp": 4.92892,
    "tbsp": 14.7868,
    "fl oz": 29.5735,
    "cup": 236.588,
    "pint": 473.176,
    "quart": 946.353,
    "gallon": 3785.41,
    "cubic inch": 16.3871,
    "cubic centimeter": 1.0,
}

#: Mass units, in grams. `lb` and `oz` are exact by the 1959 international
#: yard-and-pound agreement, which is why mass<->mass is `measured`, not an
#: approximation.
MASS_G = {
    "g": 1.0,
    "kg": 1000.0,
    "mg": 0.001,
    "oz": 28.349523125,
    "lb": 453.59237,
}

#: Discrete things a recipe counts. There is no honest millilitre value for a
#: clove, so these NEVER get a physical conversion — they resolve only against
#: a portion record, and then the method is `estimated`.
COUNT_UNITS = {
    "piece", "clove", "slice", "leaf", "stalk", "sprig", "bunch", "head",
    "stick", "wedge", "fillet", "breast", "link", "loaf", "muffin", "cookie",
    "cracker", "biscuit", "pat", "cube", "ear", "ring", "floret", "serving",
}

#: Packaging. A package unit is a statement about SOMEONE'S PACKAGE, not about
#: the food, so it is never resolved from the food reference data — see
#: `convert.PACKAGE_REFUSAL_NOTE`.
PACKAGE_UNITS = {
    "can", "tin", "jar", "bottle", "package", "packet", "box", "container",
    "carton", "bag", "sachet", "tub", "pouch", "envelope",
}

#: Size words are first-class: "3 medium onions" and "3 large onions" are
#: different weights and the dataset carries both.
SIZE_WORDS = {
    "extra small", "small", "medium", "large", "extra large", "jumbo",
    "mini", "giant", "baby",
}

_UNIT_ALIASES = {
    "milliliter": "ml", "millilitre": "ml", "milliliters": "ml",
    "millilitres": "ml", "cc": "ml",
    "liter": "l", "litre": "l", "liters": "l", "litres": "l",
    "teaspoon": "tsp", "teaspoons": "tsp", "ts": "tsp", "t": "tsp",
    "tablespoon": "tbsp", "tablespoons": "tbsp", "tbs": "tbsp", "tbl": "tbsp",
    "cups": "cup", "c": "cup",
    "fluid ounce": "fl oz", "fluid ounces": "fl oz", "floz": "fl oz",
    "fl. oz.": "fl oz", "fl. oz": "fl oz", "fluid oz": "fl oz",
    "pints": "pint", "pt": "pint",
    "quarts": "quart", "qt": "quart",
    "gallons": "gallon", "gal": "gallon",
    "gram": "g", "grams": "g", "gramme": "g", "grammes": "g", "gm": "g",
    "gr": "g",
    "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg",
    "kgs": "kg",
    "milligram": "mg", "milligrams": "mg",
    "ounce": "oz", "ounces": "oz", "ozs": "oz",
    "pound": "lb", "pounds": "lb", "lbs": "lb", "#": "lb",
    # Count/piece synonyms. USDA writes a whole fruit as "fruit", a recipe
    # writes "1 orange" — both mean one piece.
    "fruit": "piece", "whole": "piece", "each": "piece", "item": "piece",
    "pieces": "piece", "unit": "piece",
    "cloves": "clove", "slices": "slice", "leaves": "leaf",
    "stalks": "stalk", "sprigs": "sprig", "bunches": "bunch",
    "heads": "head", "sticks": "stick", "wedges": "wedge",
    "fillets": "fillet", "breasts": "breast", "links": "link",
    "loaves": "loaf", "muffins": "muffin", "cookies": "cookie",
    "crackers": "cracker", "biscuits": "biscuit", "pats": "pat",
    "cubes": "cube", "ears": "ear", "rings": "ring", "florets": "floret",
    "servings": "serving",
    "cans": "can", "tins": "tin", "jars": "jar", "bottles": "bottle",
    "packages": "package", "pkg": "package", "pkgs": "package",
    "packets": "packet", "boxes": "box", "containers": "container",
    "cartons": "carton", "bags": "bag", "sachets": "sachet", "tubs": "tub",
    "pouches": "pouch", "envelopes": "envelope",
}


def normalise_unit(raw: str | None) -> str | None:
    """Map any spelling of a unit onto its canonical key, or None."""
    u = (raw or "").strip().lower().rstrip(".")
    if not u:
        return None
    u = _UNIT_ALIASES.get(u, u)
    if u in VOLUME_ML or u in MASS_G or u in COUNT_UNITS or u in PACKAGE_UNITS:
        return u
    return None


def unit_kind(unit: str | None) -> str | None:
    """'volume' | 'mass' | 'count' | 'package' | None."""
    if unit is None:
        return None
    if unit in VOLUME_ML:
        return "volume"
    if unit in MASS_G:
        return "mass"
    if unit in COUNT_UNITS:
        return "count"
    if unit in PACKAGE_UNITS:
        return "package"
    return None


def measure_key(raw: str | None) -> str:
    """The string a measure is STORED and LOOKED UP under in a dataset file.

    Canonical when we recognise the unit (so ``"liter"`` and ``"litres"`` land
    on the same row as ``"l"``), and merely trimmed-and-lowercased when we do
    not. The permissive tail matters: a host app building its own dataset may
    legitimately have a measure foodcore has no opinion about ("scoop"), and
    dropping it would lose data the host put there on purpose.
    """
    return normalise_unit(raw) or (raw or "").strip().lower()


def singularise(word: str) -> str:
    """Crude but sufficient for food descriptions: tomatoes->tomato, whites->white."""
    w = word
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("oes"):
        return w[:-2]
    if len(w) > 3 and w.endswith("ses"):
        return w[:-2]
    if len(w) > 2 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokenise(text: str) -> tuple[str, ...]:
    """Lowercase alphanumeric tokens, singularised, order preserved."""
    words = re.findall(r"[a-z0-9%]+", (text or "").lower())
    return tuple(singularise(w) for w in words if w)


def extract_size(tokens: list[str]) -> str | None:
    """Pull a size word out of `tokens` (mutating it), handling 'extra large'."""
    for i, tok in enumerate(tokens):
        if tok == "extra" and i + 1 < len(tokens):
            pair = f"extra {tokens[i + 1]}"
            if pair in SIZE_WORDS:
                del tokens[i:i + 2]
                return pair
        if tok in SIZE_WORDS:
            del tokens[i]
            return tok
    return None
