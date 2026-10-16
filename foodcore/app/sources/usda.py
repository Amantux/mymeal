"""USDA FoodData Central adapter — portion weights for ~10,000 foods, CC0.

Why this module exists: myMeal converts volume to mass through a hand-maintained
table of 41 densities keyed by substring (`backend/app/services/units.py`). That
table cannot answer "how much does one medium onion weigh", it has no provenance,
and a substring key once weighed rice vinegar as rice. FDC publishes *measured*
portion weights — "1 cup, chopped = 160 g", "1 clove = 3 g" — for every food in
the dataset, which is the reference floor the platform note calls the single
highest-value import.

**This module PARSES; it does not store.** It reads an FDC CSV export into the
record types `datasets.py` defines (`FoodRecord`, `Portion`) and hands them to
`datasets.build_dataset`, which owns the one storage format. There was briefly a
second importer (`usda_import.py`) with its own `Dataset`, its own unit table
and its own parse of the same five CSVs; it read the schema more naively and
silently lost the two traps below, so it is gone. One parser, one store.

Pure data in, pure data out: no Flask, no ORM, no network, no clock. The loader
takes a directory of FDC CSV files, so production points it at the real export
and tests point it at `foodcore/tests/fixtures/usda_fdc_sample`.

THE SCHEMA, as actually published (verified against the real exports, not from
memory — see `docs` note in the report):

    food.csv          fdc_id, data_type, description, food_category_id,
                      publication_date
    food_portion.csv  id, fdc_id, seq_num, amount, measure_unit_id,
                      portion_description, modifier, gram_weight, data_points,
                      footnote, min_year_acquired
    measure_unit.csv  id, name
    nutrient.csv      id, name, unit_name, nutrient_nbr, rank
    food_nutrient.csv id, fdc_id, nutrient_id, amount, data_points,
                      derivation_id, min, max, median, footnote,
                      min_year_acquired

Two traps in that schema, both handled here:

1. **The unit is not always in `measure_unit_id`.** SR Legacy parks every
   portion on id 9999 ("undetermined") and writes the unit as free text in
   `modifier` ("cup, chopped", "stalk, medium (7-1/2\" long)"). Foundation Foods
   populates `measure_unit_id` properly and leaves `modifier` empty. Reading
   only one of the two columns silently loses most of the dataset's portions.
2. **`amount` is not always 1.** `amount=3, modifier="cloves", gram_weight=9`
   means 3 g per clove, and `amount=0.5, modifier="cup", gram_weight=120` means
   240 g per cup. Treating `gram_weight` as per-unit is wrong by 3x and 2x
   respectively on real rows in the fixture.

A third trap, which is really trap 1's consequence: the preparation qualifier
("chopped", "sliced", "packed") must survive parsing and survive STORAGE, or a
bare "1 cup onion" silently picks between 160 g and 115 g. That is why the
dataset file carries `size` and `qualifier` columns rather than one flat
measure name.
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field

from ..datasets import FoodRecord, Portion
from ..registry import SourceRecord
from ..units import extract_size, measure_key, normalise_unit

#: Must be a member of provenance.SOURCES — the closed provider vocabulary.
#: The dataset EDITION ("sr_legacy_food/2018-04+...") lives in
#: SourceRecord.dataset_version and the row id in source_id, so nothing is lost
#: by naming the provider plainly here.
SOURCE = "usda"
#: USDA FoodData Central is public domain. Hardcoded rather than taken from a
#: flag: letting an operator relabel the licence would let a share-alike dataset
#: be laundered into a CC0 claim.
LICENSE = "CC0-1.0"

#: USDA's nutrient names → foodcore's nutrient keys. Matched lower-cased.
#: Extend deliberately: a key that appears here becomes part of the dataset
#: contract that engines query by.
#:
#: Mapped by NAME, not by numeric id. Ids are stable in practice but are a thing
#: we would be asserting from memory; a name map is inspectable, and anything
#: unmapped is counted and reported rather than silently dropped.
NUTRIENT_NAMES: dict[str, str] = {
    "energy": "energy",
    "protein": "protein",
    "total lipid (fat)": "fat_total",
    "fatty acids, total saturated": "fat_saturated",
    "carbohydrate, by difference": "carbohydrate",
    "fiber, total dietary": "fiber",
    # Both spellings are real: SR Legacy publishes "Sugars, Total" and the
    # newer Foundation exports publish the NLEA-qualified name for the same
    # quantity. Mapping only one silently drops sugar for half the dataset.
    "sugars, total": "sugars",
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
METHOD_CONFIDENCE = {
    "measured": 0.95,
    "calculated": 0.8,
    "declared": 0.7,
    "inferred": 0.6,
    "estimated": 0.4,
}

#: FDC's placeholder for "the unit is in the modifier text instead".
_UNDETERMINED_UNITS = {"undetermined", "", "unit"}

#: A parenthetical in a modifier is a DIMENSIONAL NOTE, not a preparation.
_PAREN_RE = re.compile(r"\([^)]*\)")
_SPLIT_RE = re.compile(r"[,;/]| or ")
_NUM_RE = re.compile(r"^[\d.\-/]+$")


class UsdaImportError(Exception):
    """The export directory is not a USDA FDC export we can read."""


def parse_portion_text(measure_unit_name: str | None, modifier: str,
                       portion_description: str) -> tuple[str | None, str | None, str]:
    """Return (unit, size, qualifier) for one `food_portion` row.

    Trap 1 from the module docstring lives here: when `measure_unit_id` points
    at "undetermined", the unit is hidden in `modifier` as free text.
    """
    unit = normalise_unit(measure_unit_name) if (
        (measure_unit_name or "").strip().lower() not in _UNDETERMINED_UNITS
    ) else None

    text = (modifier or "").strip() or (portion_description or "").strip()
    # The parenthetical is a DIMENSIONAL NOTE, not a preparation: "medium
    # (2-1/2\" dia)" is still just a medium onion. It is dropped from the
    # qualifier (and so does not make the row ineligible for a bare lookup);
    # the full text stays on `Portion.raw_text` for display and audit.
    body = _PAREN_RE.sub(" ", text)

    tokens: list[str] = []
    for chunk in _SPLIT_RE.split(body):
        tokens.extend(t for t in chunk.split() if t)
    tokens = [t.strip(".").lower() for t in tokens if not _NUM_RE.match(t)]

    size = extract_size(tokens)

    leftover: list[str] = []
    for tok in tokens:
        cand = normalise_unit(tok)
        if unit is None and cand is not None:
            unit = cand
        else:
            leftover.append(tok)

    # "1 large" / "1 medium (2-1/2in dia)" with no unit word at all means one
    # whole piece of the food — that IS the unit, and dropping it would throw
    # away every count portion in the dataset.
    if unit is None and size is not None:
        unit = "piece"

    return unit, size, " ".join(leftover).strip()


# --- Import reporting --------------------------------------------------------

@dataclass
class ImportStats:
    """What the parse saw. Printed by the CLI and stored in SourceRecord.notes.

    The point is that nothing is dropped silently: a portion we cannot name and
    an upstream nutrient we have no key for are both COUNTED, so an operator can
    see the shape of what was skipped instead of trusting a bare "done".
    """
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


# --- The parse result --------------------------------------------------------

@dataclass(frozen=True)
class FdcExport:
    """One parsed FDC export, in memory.

    Deliberately NOT a ``Dataset``: a dataset is the file a host app queries,
    and there is exactly one of those. This is the intermediate an operator's
    build passes through on its way into that file, and it is the shape the
    parser's own tests assert against. Keeping the two apart is what stopped
    foodcore having two incompatible ``Dataset`` classes.
    """
    version: str
    retrieved: str
    foods: tuple[FoodRecord, ...]
    stats: ImportStats = field(default_factory=ImportStats)
    source: str = SOURCE
    license: str = LICENSE

    def food(self, source_id: str) -> FoodRecord | None:
        wanted = str(source_id)
        for rec in self.foods:
            if rec.source_id == wanted:
                return rec
        return None

    # -- the hand-off to storage -----------------------------------------

    def source_record(self) -> SourceRecord:
        return SourceRecord(
            provider=self.source,
            dataset_version=self.version,
            retrieved_at=self.retrieved,
            license=self.license,
            record_count=len(self.foods),
            notes=self.stats.report(),
        )

    def rows(self) -> tuple[list[dict], list[dict], list[dict]]:
        """``(foods, portions, nutrients)`` in the shape ``build_dataset`` takes.

        Every semantic column the parser worked out — unit, size, qualifier,
        amount, the raw modifier text — travels through to the file. The
        previous importer flattened all of it to one `measure` string, which is
        what made "cup, chopped" (160 g) and "cup, sliced" (115 g) collide on
        one primary key and let whichever row was written last win.
        """
        foods: list[dict] = []
        portions: list[dict] = []
        nutrients: list[dict] = []
        for rec in self.foods:
            method = DATA_TYPE_METHOD.get(rec.data_type, _DEFAULT_METHOD)
            confidence = METHOD_CONFIDENCE[method]
            foods.append({
                "key": rec.key,
                "name": rec.description,
                "provider": rec.provider,
                "source_id": rec.source_id,
                "data_type": rec.data_type,
            })
            for p in rec.portions:
                portions.append({
                    "food_key": rec.key,
                    "measure": p.measure,
                    "size": p.size,
                    "qualifier": p.qualifier,
                    "amount": p.amount,
                    "grams": p.gram_weight,
                    "seq_num": p.seq_num,
                    "raw_text": p.raw_text,
                    "method": method,
                    "confidence": confidence,
                    "source_id": p.source_id,
                })
            for key, (amount, unit) in sorted(rec.nutrients.items()):
                nutrients.append({
                    "food_key": rec.key,
                    "nutrient": key,
                    "per_100g": amount,
                    "unit": unit,
                    "method": method,
                    "confidence": confidence,
                    "source_id": rec.source_id,
                })
        return foods, portions, nutrients


# --- Loading -----------------------------------------------------------------

_VERSION_RE = re.compile(r"FoodData_Central_(.+)_csv_(\d{4}-\d{2}(?:-\d{2})?)",
                         re.IGNORECASE)


def _read(path: str, filename: str, stats: ImportStats) -> list[dict[str, str]]:
    full = os.path.join(path, filename)
    if not os.path.isfile(full):
        stats.missing_files.append(filename)
        return []
    with open(full, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _num(raw: str | None) -> float | None:
    try:
        return float((raw or "").strip())
    except (TypeError, ValueError):
        return None


def _int(raw: str | None) -> int | None:
    v = _num(raw)
    return int(v) if v is not None else None


def infer_version(directory: str) -> str:
    """'sr_legacy_food/2018-04' from the export's own directory name.

    The CSV export carries no version stamp anywhere inside it, and a dataset
    version is load-bearing for a citable provenance, so it is inferred from the
    published directory name and otherwise must be passed explicitly.
    """
    m = _VERSION_RE.search(os.path.basename(os.path.normpath(directory)))
    return f"{m.group(1)}/{m.group(2)}" if m else "unknown"


def load_fdc_csv(directory: str, *, version: str | None = None,
                 retrieved: str | None = None,
                 data_types: tuple[str, ...] | None = None,
                 limit: int | None = None,
                 nutrient_keys: set[str] | None = None) -> FdcExport:
    """Parse an FDC CSV export directory into an `FdcExport`.

    `retrieved` defaults to the latest `publication_date` in `food.csv` — a real
    value from the data itself, so loading the same export twice yields the same
    provenance rather than today's clock reading.
    """
    stats = ImportStats()
    if not os.path.isdir(directory):
        raise UsdaImportError(f"no such directory: {directory!r}")

    units = {r["id"]: r["name"]
             for r in _read(directory, "measure_unit.csv", stats)}

    # food.csv first: a portion or nutrient row for a food we filtered out is
    # not an error, it is just not ours.
    wanted: dict[str, str] = {}      # fdc_id -> data_type
    descriptions: dict[str, str] = {}
    order: list[str] = []
    latest = ""
    for r in _read(directory, "food.csv", stats):
        fdc_id = (r.get("fdc_id") or "").strip()
        description = (r.get("description") or "").strip()
        data_type = (r.get("data_type") or "").strip()
        if not fdc_id or not description:
            continue
        if data_types and data_type not in data_types:
            continue
        if limit is not None and len(order) >= limit:
            break
        latest = max(latest, (r.get("publication_date") or "").strip())
        wanted[fdc_id] = data_type
        descriptions[fdc_id] = description
        order.append(fdc_id)

    if not order:
        raise UsdaImportError(
            f"{directory!r} yielded no foods. Expected a USDA FoodData Central "
            "CSV export containing food.csv."
        )

    portions: dict[str, list[Portion]] = {}
    for r in _read(directory, "food_portion.csv", stats):
        fdc_id = (r.get("fdc_id") or "").strip()
        if fdc_id not in wanted:
            continue
        grams = _num(r.get("gram_weight"))
        amount = _num(r.get("amount")) or 1.0
        unit, size, qualifier = parse_portion_text(
            units.get((r.get("measure_unit_id") or "").strip()),
            r.get("modifier", ""),
            r.get("portion_description", ""),
        )
        if unit is None or grams is None or grams <= 0 or amount <= 0:
            # A portion we cannot name, or cannot weigh, is one an engine must
            # refuse. Counted, not guessed at: inventing "serving" here is
            # exactly the fabrication this package exists to prevent.
            stats.skipped_portions += 1
            continue
        portions.setdefault(fdc_id, []).append(Portion(
            food_key=f"{SOURCE}:{fdc_id}",
            measure=measure_key(unit),
            size=size,
            qualifier=qualifier,
            amount=amount,
            gram_weight=grams,
            source_id=(r.get("id") or "").strip(),
            seq_num=_int(r.get("seq_num")),
            raw_text=(r.get("modifier") or r.get("portion_description") or "").strip(),
        ))

    nutrient_map: dict[str, tuple[str, str]] = {}
    unmapped_names: dict[str, str] = {}
    for r in _read(directory, "nutrient.csv", stats):
        nid = (r.get("id") or "").strip()
        name = (r.get("name") or "").strip()
        key = NUTRIENT_NAMES.get(name.lower())
        if key is None or (nutrient_keys is not None and key not in nutrient_keys):
            unmapped_names[nid] = name
            continue
        unit = (r.get("unit_name") or "").strip().lower() or "g"
        # FDC writes energy in KCAL (and sometimes kJ rows we do not map);
        # normalising the spelling here keeps the dataset's unit column a
        # vocabulary rather than whatever case upstream used.
        nutrient_map[nid] = (key, "kcal" if key == "energy" and unit in {"kcal", ""}
                             else unit)

    nutrients: dict[str, dict[str, tuple[float, str]]] = {}
    for r in _read(directory, "food_nutrient.csv", stats):
        fdc_id = (r.get("fdc_id") or "").strip()
        if fdc_id not in wanted:
            continue
        nid = (r.get("nutrient_id") or "").strip()
        mapped = nutrient_map.get(nid)
        if mapped is None:
            name = unmapped_names.get(nid, nid)
            stats.unmapped_nutrients[name] = stats.unmapped_nutrients.get(name, 0) + 1
            continue
        amount = _num(r.get("amount"))
        if amount is None:
            # An absent amount is NOT zero. Dropping the row makes the lookup
            # return Unresolved, which is the honest answer.
            continue
        key, unit = mapped
        nutrients.setdefault(fdc_id, {})[key] = (amount, unit)

    foods = tuple(
        FoodRecord(
            key=f"{SOURCE}:{fdc_id}",
            description=descriptions[fdc_id],
            provider=SOURCE,
            source_id=fdc_id,
            data_type=wanted[fdc_id],
            portions=tuple(portions.get(fdc_id, ())),
            nutrients=nutrients.get(fdc_id, {}),
        )
        for fdc_id in sorted(order)
    )

    stats.foods = len(foods)
    stats.portions = sum(len(f.portions) for f in foods)
    stats.nutrients = sum(len(f.nutrients) for f in foods)

    return FdcExport(
        version=version or infer_version(directory),
        retrieved=retrieved or latest,
        foods=foods,
        stats=stats,
    )
