"""USDA FoodData Central adapter — portion weights for ~10,000 foods, CC0.

Why this module exists: myMeal converts volume to mass through a hand-maintained
table of 41 densities keyed by substring (`backend/app/services/units.py`). That
table cannot answer "how much does one medium onion weigh", it has no provenance,
and a substring key once weighed rice vinegar as rice. FDC publishes *measured*
portion weights — "1 cup, chopped = 160 g", "1 clove = 3 g" — for every food in
the dataset, which is the reference floor the platform note calls the single
highest-value import.

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
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field

#: Must be a member of provenance.SOURCES — the closed provider vocabulary.
#: The dataset EDITION ("sr_legacy_food/2018-04+...") lives in Dataset.version
#: and the row id in source_id, so nothing is lost by naming the provider
#: plainly here.
SOURCE = "usda"
LICENSE = "CC0-1.0"

# --- Unit vocabulary ---------------------------------------------------------
# This lives in the adapter because the adapter is what must normalise USDA's
# own free-text modifiers, and `convert.py` imports it from here so there is
# exactly ONE spelling table in foodcore rather than the two the estate already
# has (myMeal's `units.py` and Edibl's `quantity.py`).

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

#: FDC's placeholder for "the unit is in the modifier text instead".
_UNDETERMINED_UNITS = {"undetermined", "", "unit"}

#: Preparation words that qualify a portion without changing which unit it is.
#: Kept on the record (a cup of chopped onion is not a cup of sliced onion) but
#: never treated as the unit.
_PAREN_RE = re.compile(r"\([^)]*\)")
_SPLIT_RE = re.compile(r"[,;/]| or ")
_NUM_RE = re.compile(r"^[\d.\-/]+$")


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


def _extract_size(tokens: list[str]) -> str | None:
    """Pull a size word out of `tokens`, handling the two-word 'extra large'."""
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
    # the full text stays on `Portion.raw_modifier` for display and audit.
    body = _PAREN_RE.sub(" ", text)

    tokens: list[str] = []
    for chunk in _SPLIT_RE.split(body):
        tokens.extend(t for t in chunk.split() if t)
    tokens = [t.strip(".").lower() for t in tokens if not _NUM_RE.match(t)]

    size = _extract_size(tokens)

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


# --- Records -----------------------------------------------------------------

@dataclass(frozen=True)
class Portion:
    """One `food_portion` row, normalised. `grams_per_unit` is the useful bit."""
    portion_id: str
    fdc_id: str
    unit: str | None
    size: str | None
    qualifier: str
    amount: float
    gram_weight: float
    data_points: int | None
    raw_modifier: str
    seq_num: int | None = None

    @property
    def grams_per_unit(self) -> float:
        """Trap 2: divide by `amount`. '3 cloves = 9 g' is 3 g per clove."""
        return self.gram_weight / self.amount if self.amount else 0.0


#: States that must never be collapsed into one another. Spec FR-01's regression
#: list names "1 cup rice" raw vs cooked explicitly: 185 g vs 158 g in the real
#: data, an 17% error if they merge.
STATES = ("raw", "cooked", "dry", "canned", "frozen", "dried", "toasted", "boiled")


@dataclass(frozen=True)
class FoodRecord:
    fdc_id: str
    description: str
    data_type: str
    category_id: str
    publication_date: str
    portions: tuple[Portion, ...] = ()
    #: nutrient_id -> (amount per 100 g, unit_name). Loaded, not interpreted —
    #: nutrition arithmetic is a sibling module's job.
    nutrients: dict[str, tuple[float, str]] = field(default_factory=dict)

    @property
    def head(self) -> tuple[str, ...]:
        """Tokens before the first comma: USDA's head noun ('Wheat flour')."""
        return tokenise(self.description.split(",")[0])

    @property
    def tokens(self) -> tuple[str, ...]:
        return tokenise(self.description)

    @property
    def states(self) -> frozenset[str]:
        return frozenset(s for s in STATES if s in self.tokens)

    def portion(self, unit: str, size: str | None = None) -> Portion | None:
        """Best portion for `unit` (+ `size` when given), or None.

        **Only UNQUALIFIED rows are eligible.** This is the refuse-rather-than-
        fabricate rule applied to portion selection, and it is not fussiness:
        onions publish "cup, chopped" 160 g and "cup, sliced" 115 g and no plain
        cup, cream publishes "cup, whipped" 120 g and "cup, fluid" 238 g, and
        cheddar publishes shredded 113 g / diced 132 g / melted 244 g. Letting a
        bare "1 cup" pick whichever row sorts first is a silent 2x error, so a
        bare "1 cup onion" is treated as genuinely under-specified instead (see
        `qualified_portions`).

        Ties break on seq_num then portion_id, so the answer is stable across
        loads rather than dependent on CSV row order.
        """
        same_unit = [p for p in self.portions
                     if p.unit == unit and p.gram_weight > 0 and not p.qualifier]
        if not same_unit:
            return None

        def rank(p: Portion) -> tuple[int, str]:
            return (p.seq_num if p.seq_num is not None else 9999, p.portion_id)

        pool = [p for p in same_unit if p.size == size]
        return sorted(pool, key=rank)[0] if pool else None

    def sized_portions(self, unit: str) -> tuple[Portion, ...]:
        return tuple(p for p in self.portions if p.unit == unit and p.size is not None)

    def qualified_portions(self, unit: str) -> tuple[Portion, ...]:
        """Rows for `unit` that carry a preparation qualifier — the ones
        `portion()` deliberately refuses to choose between."""
        return tuple(p for p in self.portions
                     if p.unit == unit and p.qualifier and p.size is None)

    def volume_portion(self) -> Portion | None:
        """Any unqualified volume portion, for deriving a density when the
        asked-for volume unit has no portion row of its own (2 litres of milk
        from the published quart)."""
        vols = [p for p in self.portions
                if p.unit in VOLUME_ML and p.size is None and p.gram_weight > 0
                and not p.qualifier]
        if not vols:
            return None
        # Biggest measured volume = smallest relative measurement error.
        return sorted(
            vols,
            key=lambda p: (-VOLUME_ML[p.unit] * p.amount, p.portion_id),
        )[0]


# --- Food resolution (name -> record) ---------------------------------------

@dataclass(frozen=True)
class FoodMatch:
    """Outcome of resolving a name. `confident` gates every write-ish use."""
    query: str
    confident: bool
    candidates: tuple[tuple[FoodRecord, int], ...]

    @property
    def food(self) -> FoodRecord | None:
        return self.candidates[0][0] if (self.confident and self.candidates) else None

    def describe(self, limit: int = 5) -> str:
        """Candidate list for an Unresolved detail — the metadata that actually
        settles it for a human: the description and the state."""
        out = []
        for rec, score in self.candidates[:limit]:
            states = "/".join(sorted(rec.states)) or "unspecified"
            out.append(f"{rec.fdc_id} {rec.description} [{states}] score={score}")
        return "; ".join(out)


#: Scores are by KIND of evidence with wide gaps, per the estate's resolution
#: rule: a stronger kind of match always beats a pile of weaker ones.
_SCORE_HEAD_EXACT = 100     # query IS the head noun ("butter" -> "Butter, salted")
_SCORE_HEAD_SUBSET = 70     # query inside the head ("butter" -> "Peanut butter, ...")
_SCORE_ANYWHERE = 50        # query somewhere in the full description


@dataclass(frozen=True)
class Dataset:
    """A loaded FDC export. Read-only; safe to share between threads."""
    version: str
    retrieved: str
    foods: tuple[FoodRecord, ...]
    source: str = SOURCE
    license: str = LICENSE

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {f.fdc_id: f for f in self.foods})

    def food(self, fdc_id: str) -> FoodRecord | None:
        return getattr(self, "_by_id").get(str(fdc_id))

    def resolve_food(self, query: str) -> FoodMatch:
        """Confidence-tiered name resolution. Refuses rather than guesses.

        The load-bearing rule is **unique full-token coverage**: candidates must
        contain EVERY token of the query, and if exactly one food does, that is
        the answer. It falls out of this that "rice" is ambiguous (raw rice and
        cooked rice both contain "rice", and they weigh 185 g and 158 g per cup)
        while "cooked rice" is not — which is precisely spec FR-01's rule that
        raw and cooked must not merge, rather than a rule bolted on beside it.
        """
        q = tokenise(query)
        if not q:
            return FoodMatch(query=query, confident=False, candidates=())

        direct = self.food(query.strip())
        if direct is not None:
            return FoodMatch(query=query, confident=True, candidates=((direct, 100),))

        scored: list[tuple[FoodRecord, int]] = []
        for rec in self.foods:
            all_tokens = set(rec.tokens)
            if not set(q) <= all_tokens:
                continue   # partial coverage is not evidence, it is a coincidence
            head = rec.head
            if tuple(q) == tuple(head) or set(q) == set(head):
                score = _SCORE_HEAD_EXACT
            elif set(q) <= set(head):
                score = _SCORE_HEAD_SUBSET
            else:
                score = _SCORE_ANYWHERE
            scored.append((rec, score))

        scored.sort(key=lambda rs: (-rs[1], len(rs[0].tokens), rs[0].description))
        if not scored:
            return FoodMatch(query=query, confident=False, candidates=())
        if len(scored) == 1:
            return FoodMatch(query=query, confident=True, candidates=tuple(scored))

        top, second = scored[0][1], scored[1][1]
        confident = top >= _SCORE_HEAD_SUBSET and (top - second) >= 30
        return FoodMatch(query=query, confident=confident, candidates=tuple(scored))


# --- Loading -----------------------------------------------------------------

_VERSION_RE = re.compile(r"FoodData_Central_(.+)_csv_(\d{4}-\d{2}(?:-\d{2})?)",
                         re.IGNORECASE)


def _read(path: str) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
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
    version is load-bearing for idempotent import, so it is inferred from the
    published directory name and otherwise must be passed explicitly.
    """
    m = _VERSION_RE.search(os.path.basename(os.path.normpath(directory)))
    return f"{m.group(1)}/{m.group(2)}" if m else "unknown"


def load_fdc_csv(directory: str, *, version: str | None = None,
                 retrieved: str | None = None,
                 nutrient_ids: set[str] | None = None) -> Dataset:
    """Load an FDC CSV export directory into a `Dataset`.

    `retrieved` defaults to the latest `publication_date` in `food.csv` — a real
    value from the data itself, so loading the same export twice yields the same
    provenance rather than today's clock reading.
    """
    units = {r["id"]: r["name"] for r in _read(os.path.join(directory, "measure_unit.csv"))}
    nutrient_units = {
        r["id"]: r.get("unit_name", "")
        for r in _read(os.path.join(directory, "nutrient.csv"))
    }

    portions: dict[str, list[Portion]] = {}
    for r in _read(os.path.join(directory, "food_portion.csv")):
        grams = _num(r.get("gram_weight"))
        if grams is None:
            continue
        amount = _num(r.get("amount")) or 1.0
        unit, size, qualifier = parse_portion_text(
            units.get(r.get("measure_unit_id", "")),
            r.get("modifier", ""),
            r.get("portion_description", ""),
        )
        portions.setdefault(r["fdc_id"], []).append(Portion(
            portion_id=r["id"],
            fdc_id=r["fdc_id"],
            unit=unit,
            size=size,
            qualifier=qualifier,
            amount=amount,
            gram_weight=grams,
            data_points=_int(r.get("data_points")),
            raw_modifier=(r.get("modifier") or r.get("portion_description") or "").strip(),
            seq_num=_int(r.get("seq_num")),
        ))

    nutrients: dict[str, dict[str, tuple[float, str]]] = {}
    for r in _read(os.path.join(directory, "food_nutrient.csv")):
        nid = r.get("nutrient_id", "")
        if nutrient_ids is not None and nid not in nutrient_ids:
            continue
        amt = _num(r.get("amount"))
        if amt is None:
            continue
        nutrients.setdefault(r["fdc_id"], {})[nid] = (amt, nutrient_units.get(nid, ""))

    foods: list[FoodRecord] = []
    latest = ""
    for r in _read(os.path.join(directory, "food.csv")):
        pub = (r.get("publication_date") or "").strip()
        latest = max(latest, pub)
        foods.append(FoodRecord(
            fdc_id=r["fdc_id"],
            description=(r.get("description") or "").strip(),
            data_type=(r.get("data_type") or "").strip(),
            category_id=(r.get("food_category_id") or "").strip(),
            publication_date=pub,
            portions=tuple(portions.get(r["fdc_id"], ())),
            nutrients=nutrients.get(r["fdc_id"], {}),
        ))

    return Dataset(
        version=version or infer_version(directory),
        retrieved=retrieved or latest,
        foods=tuple(sorted(foods, key=lambda f: f.fdc_id)),
    )


# --- Idempotent import -------------------------------------------------------

@dataclass(frozen=True)
class ImportStats:
    foods_inserted: int = 0
    foods_updated: int = 0
    foods_unchanged: int = 0
    portions_inserted: int = 0
    portions_unchanged: int = 0


@dataclass
class ReferenceStore:
    """In-memory destination for an import, keyed the way a table would be.

    Spec §14 wants import to be idempotent, and the only way to get that is a
    key that is a property of the DATA rather than of the import run:
    `(source, version, fdc_id)` for foods and `(source, version, portion_id)`
    for portions. Re-running the same export therefore updates nothing and
    inserts nothing, while a NEW dataset version lands alongside the old one
    instead of overwriting it — which is what makes a provenance `version`
    field meaningful.

    Deliberately a plain dict and deliberately not group-scoped: reference data
    is identical for every household on earth and has no tenants.
    """
    foods: dict[tuple[str, str, str], FoodRecord] = field(default_factory=dict)
    portions: dict[tuple[str, str, str], Portion] = field(default_factory=dict)

    def ingest(self, dataset: Dataset) -> ImportStats:
        ins = upd = same = pins = psame = 0
        for rec in dataset.foods:
            key = (dataset.source, dataset.version, rec.fdc_id)
            existing = self.foods.get(key)
            if existing is None:
                self.foods[key] = rec
                ins += 1
            elif existing == rec:
                same += 1
            else:
                self.foods[key] = rec
                upd += 1
            for p in rec.portions:
                pkey = (dataset.source, dataset.version, p.portion_id)
                if self.portions.get(pkey) == p:
                    psame += 1
                else:
                    self.portions[pkey] = p
                    pins += 1
        return ImportStats(ins, upd, same, pins, psame)
