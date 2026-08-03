"""Reading an oven temperature out of recipe text.

schema.org has no temperature field, so a scraped recipe only ever states it
inside the instructions ("Preheat the oven to 350°F"). Nothing extracted it,
which is why imported recipes had no temperature at all.

Everything here is a pure function over text. The hard part is not finding
temperatures — it is NOT finding numbers that merely look like one. A recipe is
full of them: gram weights, minutes, page references, tin sizes. So a number is
only read as a temperature when the text says so, never inferred from magnitude.
"""
from __future__ import annotations

import re

# UK ovens are marked in gas numbers, not degrees. Fixed conventional table.
GAS_MARK_C = {
    1: 140, 2: 150, 3: 170, 4: 180, 5: 190,
    6: 200, 7: 220, 8: 230, 9: 240,
}

# A number followed by an EXPLICIT unit. The unit is required — "bake at 350"
# with no unit is ambiguous (US recipes mean Fahrenheit, European ones Celsius)
# and guessing from magnitude would be wrong for exactly the recipes where it
# matters. Degrees symbol optional; the word "degrees" accepted.
_TEMP_RE = re.compile(
    r"(?P<value>\d{2,3})\s*(?:°|\s*degrees?\s*)?\s*"
    # Spelled-out units are common in scraped text ("220 degrees celsius").
    # Longest alternatives first so "celsius" wins over a bare "c".
    r"(?P<unit>celsius|centigrade|fahrenheit|[CF])\b",
    re.IGNORECASE,
)
_GAS_RE = re.compile(r"gas\s*mark\s*(?P<mark>[1-9])\b", re.IGNORECASE)

# Sanity window in Celsius. Below this is a fridge or a proving drawer, above it
# is not an oven — either way it is almost certainly a misparse.
MIN_C, MAX_C = 40, 300


def f_to_c(fahrenheit: float) -> float:
    """Unrounded on purpose. 350°F is 176.67°C; storing 177 and converting back
    gives 351°F, so the recipe's own number stops matching what we show it. The
    rounding belongs at display, once."""
    return (fahrenheit - 32) * 5 / 9


def c_to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def _to_oven_mark(value: float) -> int:
    """Round to the nearest 5 — the granularity an oven dial actually has."""
    return int(round(value / 5.0) * 5)


def parse_temperature(text: str) -> float | None:
    """The oven temperature in Celsius (unrounded), or None.

    Returns the FIRST plausible match: recipes state the oven temperature once,
    up front, and a later "reduce to 180°C" should not overwrite the number the
    cook preheats to.
    """
    if not text:
        return None

    gas = _GAS_RE.search(text)
    temp = _TEMP_RE.search(text)
    # Whichever appears first in the text — "180°C (gas mark 4)" and
    # "gas mark 4 (350°F)" are both common orderings.
    if gas and (not temp or gas.start() < temp.start()):
        return float(GAS_MARK_C[int(gas.group("mark"))])

    for m in _TEMP_RE.finditer(text):
        value = int(m.group("value"))
        is_fahrenheit = m.group("unit").upper().startswith("F")
        celsius = f_to_c(value) if is_fahrenheit else float(value)
        if MIN_C <= celsius <= MAX_C:
            return celsius
    return None


def format_temperature(celsius: float | None) -> str:
    """"175°C / 350°F" — both units, because a household has one oven and its
    dial is marked in one of them, and we do not ask which.

    Both sides are rounded to the nearest 5 from the stored value, so a recipe
    that said 350°F still reads 350°F here rather than drifting to 351.
    """
    if not celsius:
        return ""
    return f"{_to_oven_mark(celsius)}°C / {_to_oven_mark(c_to_f(celsius))}°F"
