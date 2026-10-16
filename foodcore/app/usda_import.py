"""Read a USDA FoodData Central CSV export into dataset rows.

Scope note, so this does not collide with ``sources/usda.py``: that module owns
the *semantic* layer (matching a human name onto an FDC food, aliases, API
access). This module owns the **bulk import** — turning the files in an FDC
"Full Download" onto the compact rows ``datasets.build_dataset`` writes. It runs
on an operator's machine via the CLI, never in a request path.

Expected files in ``--fdc-dir`` (whatever the export contains; missing optional
files are reported, not fatal):

* ``food.csv``          — ``fdc_id, data_type, description, …``
* ``measure_unit.csv``  — ``id, name``
* ``food_portion.csv``  — ``fdc_id, amount, measure_unit_id, portion_description,
                           modifier, gram_weight, …``
* ``nutrient.csv``      — ``id, name, unit_name, …``
* ``food_nutrient.csv`` — ``fdc_id, nutrient_id, amount, …``

Nutrients are mapped by **name**, not by numeric id. Ids are stable in practice
but are a thing we would be asserting from memory; a name map is inspectable,
and anything unmapped is counted and reported rather than silently dropped.
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

#: USDA's nutrient names → foodcore's nutrient keys. Lower-cased comparison.
#: Extend deliberately: a key that appears here becomes part of the dataset
#: contract that engines query by.
NUTRIENT_NAMES: dict[str, str] = {
    "energy": "energy",
    "protein": "protein",
    "total lipid (fat)": "fat_total",
    "fatty acids, total saturated": "fat_saturated",
    "carbohydrate, by difference": "carbohydrate",
    "fiber, total dietary": "fiber",
    "sugars, total including nlea": "sugars",
    "sodium, na": "sodium",
    "calcium, ca": "calcium",
    "iron, fe": "iron",
}

#: How a value from each FDC data type came to be known. Foundation and SR
#: Legacy are laboratory analyses; branded values are what a manufacturer
#: declared on a label, which is a weaker kind of evidence and must not be
#: recorded as if somebody had measured it.
DATA_TYPE_METHOD: dict[str, str] = {
    "foundation_food": "measured",
    "sr_legacy_food": "measured",
    "survey_fndds_food": "calculated",
    "agricultural_acquisition": "measured",
    "branded_food": "declared",
    "sub_sample_food": "measured",
    "market_acquisition": "measured",
}
_DEFAULT_METHOD = "declared"

#: Confidence by method. Deliberately coarse: these are kinds of evidence, not
#: probabilities, and a precise-looking number here would be false precision.
_METHOD_CONFIDENCE = {
    "measured": 0.95,
    "calculated": 0.8,
    "declared": 0.7,
    "inferred": 0.6,
    "estimated": 0.4,
}

PROVIDER = "usda"

#: A leading quantity in a free-text portion description ("1 cup", "1 1/2 cup").
_LEADING_QUANTITY = re.compile(r"^[\d]+(?:[\s/.\d]*)")

_PLURALS = {
    "cups": "cup", "tablespoons": "tablespoon", "tbsp": "tablespoon",
    "teaspoons": "teaspoon", "tsp": "teaspoon", "slices": "slice",
    "pieces": "piece", "ounces": "ounce", "oz": "ounce",
    "fl oz": "fluid ounce", "fl. oz.": "fluid ounce", "grams": "gram",
    "liters": "liter", "litres": "liter", "milliliters": "milliliter",
    "quarts": "quart", "pints": "pint", "gallons": "gallon",
}


@dataclass
class ImportStats:
    foods: int = 0
    portions: int = 0
    nutrients: int = 0
    skipped_portions: int = 0
    unmapped_nutrients: dict[str, int] = field(default_factory=dict)
    missing_files: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [
            f"foods:     {self.foods}",
            f"portions:  {self.portions} ({self.skipped_portions} skipped)",
            f"nutrients: {self.nutrients}",
        ]
        if self.missing_files:
            lines.append("missing files: " + ", ".join(sorted(self.missing_files)))
        if self.unmapped_nutrients:
            top = sorted(self.unmapped_nutrients.items(), key=lambda kv: -kv[1])[:10]
            lines.append(
                "unmapped nutrients (add to NUTRIENT_NAMES to include): "
                + ", ".join(f"{name} x{count}" for name, count in top)
            )
        return "\n".join(lines)


class UsdaImportError(Exception):
    """The export directory is not a USDA FDC export we can read."""


def _rows(fdc_dir: str, filename: str, stats: ImportStats) -> Iterator[dict[str, str]]:
    path = os.path.join(fdc_dir, filename)
    if not os.path.isfile(path):
        stats.missing_files.append(filename)
        return
    with open(path, newline="", encoding="utf-8-sig") as fh:
        yield from csv.DictReader(fh)


def normalize_measure(unit_name: str, portion_description: str = "",
                      modifier: str = "") -> str | None:
    """One normalised household-measure name, or None if there isn't one.

    FDC spells the measure in three different places depending on the data
    type: a ``measure_unit`` row, a free-text ``portion_description``, or a
    ``modifier``. Returning None (rather than a guess like "serving") is the
    point: a portion we cannot name is a portion an engine must refuse, and
    that refusal is a legitimate ``Unresolved`` rather than a bad number.
    """
    for candidate in (unit_name, portion_description, modifier):
        text = (candidate or "").strip().lower().strip(".")
        if not text or text in {"undetermined", "", "serving", "quantity not specified"}:
            continue
        text = _PLURALS.get(text, text)
        # Free-text descriptions like "1 cup, chopped" or "0.5 cup sliced" keep
        # only the measure: the quantity is already in the `amount` column, so
        # leaving it in the measure name would make every lookup miss.
        text = text.split(",")[0].strip()
        text = _LEADING_QUANTITY.sub("", text).strip()
        text = _PLURALS.get(text, text)
        if text and len(text) <= 40:
            return text
    return None


def collect(fdc_dir: str, *, data_types: tuple[str, ...] | None = None,
            limit: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                                               list[dict[str, Any]], ImportStats]:
    """Read an FDC export into ``(foods, portions, nutrients, stats)``."""
    stats = ImportStats()
    if not os.path.isdir(fdc_dir):
        raise UsdaImportError(f"no such directory: {fdc_dir!r}")

    methods: dict[str, str] = {}
    foods: list[dict[str, Any]] = []
    for row in _rows(fdc_dir, "food.csv", stats):
        data_type = (row.get("data_type") or "").strip()
        if data_types and data_type not in data_types:
            continue
        fdc_id = (row.get("fdc_id") or "").strip()
        description = (row.get("description") or "").strip()
        if not fdc_id or not description:
            continue
        if limit is not None and len(foods) >= limit:
            break
        methods[fdc_id] = DATA_TYPE_METHOD.get(data_type, _DEFAULT_METHOD)
        foods.append({
            "key": f"{PROVIDER}:{fdc_id}",
            "name": description,
            "provider": PROVIDER,
            "source_id": fdc_id,
        })
    if not foods:
        raise UsdaImportError(
            f"{fdc_dir!r} yielded no foods. Expected a USDA FoodData Central CSV "
            "export containing food.csv."
        )

    units = {
        (row.get("id") or "").strip(): (row.get("name") or "").strip()
        for row in _rows(fdc_dir, "measure_unit.csv", stats)
    }

    portions: list[dict[str, Any]] = []
    for row in _rows(fdc_dir, "food_portion.csv", stats):
        fdc_id = (row.get("fdc_id") or "").strip()
        if fdc_id not in methods:
            continue
        measure = normalize_measure(
            units.get((row.get("measure_unit_id") or "").strip(), ""),
            row.get("portion_description", ""),
            row.get("modifier", ""),
        )
        grams = _number(row.get("gram_weight"))
        amount = _number(row.get("amount")) or 1.0
        if measure is None or grams is None or grams <= 0 or amount <= 0:
            stats.skipped_portions += 1
            continue
        method = methods[fdc_id]
        portions.append({
            "food_key": f"{PROVIDER}:{fdc_id}",
            "measure": measure,
            # Per ONE of the measure: FDC rows are "amount x measure = grams".
            "grams": grams / amount,
            "method": method,
            "confidence": _METHOD_CONFIDENCE[method],
            "source_id": fdc_id,
        })

    nutrient_keys: dict[str, tuple[str, str]] = {}
    for row in _rows(fdc_dir, "nutrient.csv", stats):
        name = (row.get("name") or "").strip().lower()
        key = NUTRIENT_NAMES.get(name)
        if key:
            nutrient_keys[(row.get("id") or "").strip()] = (
                key, (row.get("unit_name") or "").strip().lower() or "g"
            )

    unmapped_names = {
        (row.get("id") or "").strip(): (row.get("name") or "").strip()
        for row in _rows(fdc_dir, "nutrient.csv", stats)
    }

    nutrients: list[dict[str, Any]] = []
    for row in _rows(fdc_dir, "food_nutrient.csv", stats):
        fdc_id = (row.get("fdc_id") or "").strip()
        if fdc_id not in methods:
            continue
        nutrient_id = (row.get("nutrient_id") or "").strip()
        mapped = nutrient_keys.get(nutrient_id)
        if mapped is None:
            name = unmapped_names.get(nutrient_id, nutrient_id)
            stats.unmapped_nutrients[name] = stats.unmapped_nutrients.get(name, 0) + 1
            continue
        amount = _number(row.get("amount"))
        if amount is None:
            # An absent amount is NOT zero. Dropping the row makes the lookup
            # return Unresolved, which is the honest answer.
            continue
        key, unit = mapped
        method = methods[fdc_id]
        nutrients.append({
            "food_key": f"{PROVIDER}:{fdc_id}",
            "nutrient": key,
            "per_100g": amount,
            "unit": "kcal" if key == "energy" and unit in {"kcal", ""} else unit,
            "method": method,
            "confidence": _METHOD_CONFIDENCE[method],
            "source_id": fdc_id,
        })

    stats.foods = len(foods)
    stats.portions = len(portions)
    stats.nutrients = len(nutrients)
    return foods, portions, nutrients, stats


def _number(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
