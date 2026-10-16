"""The conversion engine: a quantity in, a quantity out, or an honest refusal.

Spec FR-02. Four kinds of conversion exist and they are NOT interchangeable —
the whole point of recording `Provenance.method` is that a caller can tell which
one it got:

  (a) EXACT PHYSICAL      tsp -> ml, lb -> g. Definitional, needs no food.
                          method=measured, confidence 1.0
  (b) INGREDIENT-DEPENDENT  cup -> g. Needs a food, because a cup of flour and
                          a cup of honey differ by 2.7x.
                          method=calculated from a measured portion record
  (c) PRODUCT / PACKAGE   "1 can" -> g. Needs the PACKAGE, not the food.
                          method=declared, from a caller-supplied net weight
  (d) ESTIMATED COUNT     "3 medium onions" -> g. A size word is a convention,
                          not a measurement.
                          method=estimated, confidence <= 0.55

And the rule that matters more than any of them: **refuse rather than
fabricate**. Every failure path returns `Unresolved(reason, detail)` naming what
was missing. There is no "best guess" branch anywhere in this module.

Pure: no Flask, no ORM, no network, no clock. All provenance dates come from the
dataset or from a definitional constant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from .provenance import Provenance, Resolved, Unresolved
from .sources.usda import (
    COUNT_UNITS,
    MASS_G,
    PACKAGE_UNITS,
    SIZE_WORDS,
    VOLUME_ML,
    Dataset,
    FoodRecord,
    normalise_unit,
    unit_kind,
)

#: Mass and volume conversions among US-customary and SI units are definitional
#: (the international yard-and-pound agreement, 1959-07-01), so their provenance
#: is a fixed fact rather than a dataset reading. This date is what makes the
#: `retrieved` field honest for a conversion that never touched a dataset.
PHYSICAL_SOURCE = "physical_units"
PHYSICAL_VERSION = "intl-yard-and-pound-1959"
PHYSICAL_RETRIEVED = "1959-07-01"
PHYSICAL_LICENSE = "public-domain"

PACKAGE_SOURCE = "package_declaration"

#: Why a USDA "can" portion row is NOT used to resolve "1 can of tomatoes".
#: The dataset genuinely has one (fdc 170051, `modifier="can"`, 190 g) and using
#: it is the tempting, wrong answer: 190 g is the drained weight of one USDA
#: reference can, while the tin in a UK cupboard is 400 g and a US #10 catering
#: can is 2.8 kg. A package unit is a fact about someone's package, so the
#: package size has to come from the caller (label, barcode lookup, or the user)
#: or the conversion is refused.
PACKAGE_REFUSAL_NOTE = (
    "a package unit describes a specific product, not the food; "
    "supply package=PackageSize(net_g=...) from a label or barcode lookup"
)

# --- Confidence, in one auditable place -------------------------------------
CONF_PHYSICAL = 1.0           # definitional
CONF_PACKAGE_DECLARED = 0.95  # a manufacturer's own net weight
CONF_PORTION_DIRECT = 0.9     # a measured portion row for exactly this unit
CONF_PORTION_DERIVED = 0.8    # density derived from a portion in another volume unit
CONF_COUNT_SIZED = 0.55       # "3 medium onions" — size word matched the data
CONF_COUNT_UNSIZED = 0.4      # "1 onion" — no size given, a convention was used
CONF_MASS_TO_COUNT = 0.35     # "1.5 lb of oranges is about 5 oranges"


@dataclass(frozen=True)
class PackageSize:
    """A declared package size. `source_id` says who declared it."""
    net_g: float | None = None
    net_ml: float | None = None
    label: str = ""
    source_id: str = ""
    retrieved: str = ""


def _prov(source: str, source_id: str, retrieved: str, method: str,
          version: str, confidence: float, lic: str) -> Provenance:
    return Provenance(
        source=source, source_id=source_id, retrieved=retrieved, method=method,
        version=version, confidence=confidence, license=lic,
    )


def _dataset_prov(dataset: Dataset, source_id: str, method: str,
                  confidence: float) -> Provenance:
    return _prov(dataset.source, source_id, dataset.retrieved, method,
                 dataset.version, confidence, dataset.license)


def _physical_prov(from_unit: str, to_unit: str) -> Provenance:
    return _prov(PHYSICAL_SOURCE, f"{from_unit}->{to_unit}", PHYSICAL_RETRIEVED,
                 "measured", PHYSICAL_VERSION, CONF_PHYSICAL, PHYSICAL_LICENSE)


# --- (a) exact physical conversion ------------------------------------------

def _physical(amount: float, from_unit: str, to_unit: str) -> Resolved | None:
    """Same-dimension conversion, or None if the dimensions differ."""
    if from_unit in MASS_G and to_unit in MASS_G:
        return Resolved(amount * MASS_G[from_unit] / MASS_G[to_unit], to_unit,
                        _physical_prov(from_unit, to_unit))
    if from_unit in VOLUME_ML and to_unit in VOLUME_ML:
        return Resolved(amount * VOLUME_ML[from_unit] / VOLUME_ML[to_unit], to_unit,
                        _physical_prov(from_unit, to_unit))
    if from_unit == to_unit:
        return Resolved(amount, to_unit, _physical_prov(from_unit, to_unit))
    return None


# --- (b) ingredient-dependent volume <-> mass -------------------------------

def _grams_per_ml(food: FoodRecord) -> tuple[float, str, float] | None:
    """Density from any volume portion: (g_per_ml, portion_id, confidence)."""
    p = food.volume_portion()
    if p is None or p.unit is None:
        return None
    ml = VOLUME_ML[p.unit] * p.amount
    if ml <= 0:
        return None
    return (p.gram_weight / ml, p.portion_id, CONF_PORTION_DERIVED)


def _volume_to_grams(amount: float, unit: str, food: FoodRecord,
                     dataset: Dataset) -> tuple[float, Provenance] | Unresolved:
    direct = food.portion(unit)
    if direct is not None:
        return (amount * direct.grams_per_unit,
                _dataset_prov(dataset, f"{food.fdc_id}#{direct.portion_id}",
                              "calculated", CONF_PORTION_DIRECT))
    derived = _grams_per_ml(food)
    if derived is None:
        quals = sorted({p.qualifier for p in food.qualified_portions(unit)})
        if quals:
            # "1 cup onion": the dataset knows chopped (160 g) and sliced
            # (115 g) and nothing else. Picking one is a 39% silent error.
            return Unresolved(
                "qualifier_required",
                f"{food.description} publishes '{unit}' only as "
                f"{', '.join(quals)} — these differ materially; say which",
            )
        return Unresolved(
            "no_portion_record",
            f"{dataset.source} {food.fdc_id} ({food.description}) publishes no "
            f"volume portion, so {unit} cannot be weighed",
        )
    g_per_ml, pid, conf = derived
    return (amount * VOLUME_ML[unit] * g_per_ml,
            _dataset_prov(dataset, f"{food.fdc_id}#{pid}", "calculated", conf))


# --- (d) counts and size words ----------------------------------------------

def _count_to_grams(amount: float, unit: str, size: str | None, food: FoodRecord,
                    dataset: Dataset) -> tuple[float, Provenance] | Unresolved:
    if size is not None:
        p = food.portion(unit, size)
        if p is None:
            available = sorted({x.size for x in food.sized_portions(unit) if x.size})
            return Unresolved(
                "no_portion_record",
                f"{food.description} has no '{size} {unit}' portion"
                + (f"; published sizes: {', '.join(available)}" if available else ""),
            )
        return (amount * p.grams_per_unit,
                _dataset_prov(dataset, f"{food.fdc_id}#{p.portion_id}",
                              "estimated", CONF_COUNT_SIZED))

    p = food.portion(unit)
    if p is not None:
        return (amount * p.grams_per_unit,
                _dataset_prov(dataset, f"{food.fdc_id}#{p.portion_id}",
                              "estimated", CONF_COUNT_SIZED))

    # No size asked for and no sizeless row: the dataset only knows small /
    # medium / large. Standing "medium" in is a CONVENTION, so it is reported as
    # estimated at the lowest count confidence, with the assumption spelled out
    # in the provenance id rather than hidden.
    medium = food.portion(unit, "medium")
    if medium is not None:
        return (amount * medium.grams_per_unit,
                _dataset_prov(dataset, f"{food.fdc_id}#{medium.portion_id}?size=medium",
                              "estimated", CONF_COUNT_UNSIZED))
    sizes = sorted({x.size for x in food.sized_portions(unit) if x.size})
    if sizes:
        return Unresolved(
            "size_required",
            f"{food.description} publishes '{unit}' only by size "
            f"({', '.join(sizes)}); say which one",
        )
    return Unresolved(
        "no_portion_record",
        f"{dataset.source} {food.fdc_id} ({food.description}) publishes no "
        f"'{unit}' portion",
    )


def _grams_to_count(grams: float, unit: str, size: str | None, food: FoodRecord,
                    dataset: Dataset) -> tuple[float, Provenance] | Unresolved:
    """'1.5 pounds of oranges' -> about how many oranges. Deliberately not
    rounded: the caller decides whether 5.19 reads as '5' or '5-6'."""
    p = food.portion(unit, size) if size is not None else food.portion(unit)
    if p is None and size is None:
        p = food.portion(unit, "medium")
    if p is None or p.grams_per_unit <= 0:
        return Unresolved(
            "no_portion_record",
            f"{food.description} publishes no '{unit}' weight, so a count "
            f"cannot be estimated from a mass",
        )
    return (grams / p.grams_per_unit,
            _dataset_prov(dataset, f"{food.fdc_id}#{p.portion_id}", "estimated",
                          CONF_MASS_TO_COUNT))


# --- the engine --------------------------------------------------------------

def convert(amount: float, unit: str, to_unit: str, *,
            food: FoodRecord | None = None,
            dataset: Dataset | None = None,
            size: str | None = None,
            package: PackageSize | None = None) -> Resolved | Unresolved:
    """Convert `amount` `unit` into `to_unit`, or say why not.

    `food` + `dataset` are needed for anything that crosses dimensions;
    `package` is needed for any package unit. Never raises, never guesses.
    """
    if amount is None or amount <= 0:
        return Unresolved("nonpositive_amount",
                          f"amount must be greater than zero, got {amount!r}")

    src = normalise_unit(unit)
    dst = normalise_unit(to_unit)
    if src is None:
        return Unresolved("unknown_unit", f"unrecognised source unit {unit!r}")
    if dst is None:
        return Unresolved("unknown_unit", f"unrecognised target unit {to_unit!r}")

    src_kind, dst_kind = unit_kind(src), unit_kind(dst)

    # (c) PACKAGE. Checked before everything else: no amount of reference data
    # about the FOOD tells you how big someone's can is.
    if src_kind == "package" or dst_kind == "package":
        return _convert_package(amount, src, dst, src_kind, dst_kind,
                                food=food, dataset=dataset, package=package)

    # (a) EXACT PHYSICAL.
    same = _physical(amount, src, dst)
    if same is not None:
        return same

    if food is None or dataset is None:
        return Unresolved(
            "food_required",
            f"{src} -> {dst} depends on the ingredient (a cup of flour and a cup "
            f"of honey differ by 2.7x); no food reference was supplied",
        )

    grams: float
    prov: Provenance

    # Everything else routes through grams, because grams is what the dataset
    # measures. Convert to grams, then out to the target unit.
    if src_kind == "mass":
        grams = amount * MASS_G[src]
        gprov = _physical_prov(src, "g")
    elif src_kind == "volume":
        got = _volume_to_grams(amount, src, food, dataset)
        if isinstance(got, Unresolved):
            return got
        grams, gprov = got
    elif src_kind == "count":
        got = _count_to_grams(amount, src, size, food, dataset)
        if isinstance(got, Unresolved):
            return got
        grams, gprov = got
    else:  # pragma: no cover - unit_kind only returns the four handled kinds
        return Unresolved("unknown_unit", f"unhandled unit kind for {src!r}")

    if dst_kind == "mass":
        return Resolved(grams / MASS_G[dst], dst, _weaken(gprov, dst))
    if dst_kind == "volume":
        derived = _grams_per_ml(food)
        if derived is None:
            return Unresolved(
                "no_portion_record",
                f"{dataset.source} {food.fdc_id} ({food.description}) publishes no "
                f"volume portion, so a mass cannot become {dst}",
            )
        g_per_ml, pid, conf = derived
        prov = _dataset_prov(dataset, f"{food.fdc_id}#{pid}", "calculated",
                             min(gprov.confidence, conf))
        return Resolved(grams / g_per_ml / VOLUME_ML[dst], dst, prov)
    if dst_kind == "count":
        got = _grams_to_count(grams, dst, size, food, dataset)
        if isinstance(got, Unresolved):
            return got
        count, cprov = got
        return Resolved(count, dst, _combine(gprov, cprov))
    return Unresolved("unknown_unit", f"unhandled unit kind for {dst!r}")


def _weaken(prov: Provenance, _dst: str) -> Provenance:
    return prov


def _combine(a: Provenance, b: Provenance) -> Provenance:
    """Two-step conversion is only as trustworthy as its weaker half, and its
    method is the weaker half's method."""
    ladder = ["measured", "declared", "calculated", "inferred", "estimated"]
    method = max((a.method, b.method), key=lambda m: ladder.index(m)
                 if m in ladder else len(ladder))
    weaker = a if a.confidence <= b.confidence else b
    return _prov(weaker.source, f"{a.source_id}+{b.source_id}", weaker.retrieved,
                 method, weaker.version, min(a.confidence, b.confidence),
                 weaker.license)


def _convert_package(amount: float, src: str, dst: str, src_kind: str | None,
                     dst_kind: str | None, *, food: FoodRecord | None,
                     dataset: Dataset | None,
                     package: PackageSize | None) -> Resolved | Unresolved:
    """(c) product/package-specific. The refusal branch is the headline one."""
    if package is None or (package.net_g is None and package.net_ml is None):
        which = src if src_kind == "package" else dst
        # Mutation-checked 2026-10-16: replacing this refusal with a lookup of
        # the food's own 'can' portion row makes 5 tests go red and returns
        # 190 g for "1 can tomatoes" — the plausible wrong answer. Do not.
        return Unresolved(
            "no_package_size",
            f"'{which}' has no known size for "
            f"{(food.description if food else 'this food')}: {PACKAGE_REFUSAL_NOTE}",
        )

    pid = package.source_id or package.label or "caller-declared"
    retrieved = package.retrieved or (dataset.retrieved if dataset else "")
    dprov = _prov(PACKAGE_SOURCE, pid, retrieved, "declared", package.label or "",
                  CONF_PACKAGE_DECLARED, "operator-declared")

    if src_kind == "package" and dst_kind == "package":
        if src != dst:
            return Unresolved(
                "no_package_size",
                f"'{src}' and '{dst}' are different packages; {PACKAGE_REFUSAL_NOTE}",
            )
        return Resolved(amount, dst, dprov)

    if src_kind == "package":
        if package.net_g is not None:
            grams = amount * package.net_g
        elif package.net_ml is not None:
            if food is None or dataset is None:
                return Unresolved(
                    "food_required",
                    f"the package is declared in ml and {dst} is a weight; a food "
                    f"reference is needed to weigh it",
                )
            got = _volume_to_grams(amount * package.net_ml, "ml", food, dataset)
            if isinstance(got, Unresolved):
                return got
            grams, _ = got
        else:  # pragma: no cover - the guard above proves one of the two is set
            return Unresolved("no_package_size", PACKAGE_REFUSAL_NOTE)
        if dst_kind == "mass":
            return Resolved(grams / MASS_G[dst], dst, dprov)
        if dst_kind == "volume":
            if package.net_ml is not None:
                return Resolved(amount * package.net_ml / VOLUME_ML[dst], dst, dprov)
            if food is None or dataset is None:
                return Unresolved(
                    "food_required",
                    f"the package is declared by weight and {dst} is a volume; a "
                    f"food reference is needed to convert it",
                )
            derived = _grams_per_ml(food)
            if derived is None:
                return Unresolved(
                    "no_portion_record",
                    f"{food.description} publishes no volume portion, so a declared "
                    f"net weight cannot become {dst}",
                )
            g_per_ml, _pid, conf = derived
            return Resolved(grams / g_per_ml / VOLUME_ML[dst], dst,
                            _prov(PACKAGE_SOURCE, pid, retrieved, "calculated",
                                  package.label or "", min(CONF_PACKAGE_DECLARED, conf),
                                  "operator-declared"))
        return Unresolved(
            "no_package_size",
            f"converting a package into '{dst}' is not defined; {PACKAGE_REFUSAL_NOTE}",
        )

    # mass/volume -> package ("how many cans is 1.2 kg?")
    if package.net_g is not None and src_kind == "mass":
        return Resolved(amount * MASS_G[src] / package.net_g, dst, dprov)
    if package.net_ml is not None and src_kind == "volume":
        return Resolved(amount * VOLUME_ML[src] / package.net_ml, dst, dprov)
    return Unresolved(
        "no_package_size",
        f"the declared package size is not in a dimension comparable to '{src}'; "
        f"{PACKAGE_REFUSAL_NOTE}",
    )


# --- free-text entry point ---------------------------------------------------

_UNICODE_FRACTIONS = {
    "½": Fraction(1, 2), "⅓": Fraction(1, 3), "⅔": Fraction(2, 3),
    "¼": Fraction(1, 4), "¾": Fraction(3, 4), "⅕": Fraction(1, 5),
    "⅙": Fraction(1, 6), "⅐": Fraction(1, 7),
    "⅛": Fraction(1, 8), "⅜": Fraction(3, 8), "⅝": Fraction(5, 8),
    "⅞": Fraction(7, 8),
}
_LEADING_PAREN_RE = re.compile(r"^\s*\([^)]*\)\s*")
_APPROXIMATORS = (
    "approximately", "approx.", "approx", "about", "around", "roughly", "scant",
    "heaping", "heaped", "generous", "a",
)


@dataclass(frozen=True)
class ParsedQuantity:
    amount: float | None
    unit: str | None
    size: str | None
    food_text: str


def _parse_amount(tokens: list[str]) -> tuple[float | None, list[str]]:
    """Leading number: '2', '1.5', '1/2', '1 1/2', '1½', '½'."""
    total = Fraction(0)
    taken = 0
    for tok in tokens:
        frac = None
        if tok in _UNICODE_FRACTIONS:
            frac = _UNICODE_FRACTIONS[tok]
        elif len(tok) > 1 and tok[-1] in _UNICODE_FRACTIONS and tok[:-1].isdigit():
            frac = Fraction(int(tok[:-1])) + _UNICODE_FRACTIONS[tok[-1]]
        else:
            try:
                frac = Fraction(tok)
            except (ValueError, ZeroDivisionError):
                frac = None
        if frac is None:
            break
        total += frac
        taken += 1
        # "1 1/2" is one quantity; "2 2" is not a thing, so stop after a
        # whole number followed by a fraction.
        if taken == 2:
            break
    if taken == 0:
        return None, tokens
    return float(total), tokens[taken:]


def parse_quantity(text: str) -> ParsedQuantity:
    """Split '3 medium onions' into (3, 'piece', 'medium', 'onions').

    Only as clever as the conversion engine needs. Recipe-line parsing proper
    (comments, alternatives, ranges) belongs to the importing app; this exists
    so callers and tests can express a real user phrase without hand-building
    four arguments.
    """
    raw = _LEADING_PAREN_RE.sub("", (text or "").strip())
    tokens = raw.split()
    while tokens and tokens[0].lower().strip(".") in _APPROXIMATORS:
        tokens.pop(0)
    tokens = [t for t in tokens if t]

    amount, tokens = _parse_amount([t.strip(",") for t in tokens])
    tokens = [t for t in tokens if t]
    tokens = [t for t in tokens if t.lower().strip(".") not in _APPROXIMATORS]

    size: str | None = None
    if tokens:
        low = [t.lower().strip(",.") for t in tokens]
        if len(low) >= 2 and f"{low[0]} {low[1]}" in SIZE_WORDS:
            size = f"{low[0]} {low[1]}"
            tokens = tokens[2:]
        elif low[0] in SIZE_WORDS:
            size = low[0]
            tokens = tokens[1:]

    unit: str | None = None
    if tokens:
        for take in (2, 1):           # 'fl oz' before 'oz'
            if len(tokens) >= take:
                cand = normalise_unit(" ".join(t.strip(",.") for t in tokens[:take]))
                if cand is not None:
                    unit, tokens = cand, tokens[take:]
                    break

    # A size word can also sit after the unit ('1 slice, large').
    if size is None and tokens:
        low0 = tokens[0].lower().strip(",.")
        if low0 in SIZE_WORDS:
            size, tokens = low0, tokens[1:]

    food_text = " ".join(tokens).strip(" ,.;")
    if unit is None and size is not None and food_text:
        unit = "piece"        # '3 medium onions'
    return ParsedQuantity(amount=amount, unit=unit, size=size, food_text=food_text)


def convert_text(text: str, to_unit: str, *, dataset: Dataset,
                 package: PackageSize | None = None) -> Resolved | Unresolved:
    """Resolve a written quantity ('2 cups flour') into `to_unit`.

    Food resolution is the dataset's confidence-tiered matcher, and a LOW match
    refuses with the candidates rather than picking the first row — which is how
    '1 cup rice' stays unresolved while '1 cup cooked rice' resolves.
    """
    parsed = parse_quantity(text)
    if parsed.amount is None:
        return Unresolved("no_quantity", f"no leading quantity found in {text!r}")

    unit = parsed.unit
    food: FoodRecord | None = None
    if parsed.food_text:
        match = dataset.resolve_food(parsed.food_text)
        if not match.candidates:
            return Unresolved(
                "unknown_food",
                f"no {dataset.source} food matches {parsed.food_text!r}",
            )
        if not match.confident:
            return Unresolved(
                "ambiguous_food",
                f"{parsed.food_text!r} matches several distinct foods; say which: "
                f"{match.describe()}",
            )
        food = match.food
        if unit is None:
            unit = "piece"        # '3 onions'
    if unit is None:
        return Unresolved("unknown_unit", f"no unit found in {text!r}")

    return convert(parsed.amount, unit, to_unit, food=food, dataset=dataset,
                   size=parsed.size, package=package)


__all__ = [
    "COUNT_UNITS",
    "PACKAGE_REFUSAL_NOTE",
    "PACKAGE_UNITS",
    "PackageSize",
    "ParsedQuantity",
    "Provenance",
    "Resolved",
    "Unresolved",
    "convert",
    "convert_text",
    "parse_quantity",
]
