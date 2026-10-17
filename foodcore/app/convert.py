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

from .datasets import Dataset, FoodRecord
from .provenance import Provenance, Resolved, Unresolved
from .units import (
    COUNT_UNITS,
    MASS_G,
    PACKAGE_UNITS,
    SIZE_WORDS,
    VOLUME_ML,
    normalise_unit,
    unit_kind,
)

#: Mass and volume conversions among US-customary and SI units are definitional
#: (the international yard-and-pound agreement, 1959-07-01), so their provenance
#: is a fixed fact rather than a dataset reading. This date is what makes the
#: `retrieved` field honest for a conversion that never touched a dataset.
#: `source` is a CLOSED vocabulary (provenance.SOURCES) naming WHERE data came
#: from — a dataset provider, or `internal` for rules foodcore itself asserts.
#: A unit definition is the second kind, so the specificity moves to
#: `source_id`, which is exactly what that field is for. Widening SOURCES with
#: one entry per kind of internal rule would turn a 4-value provider vocabulary
#: into an open-ended grab bag and make cross-source reasoning impossible.
#: The transformation version that appears in Provenance.version. It is what
#: lets a stored gram value be traced to the rule that produced it, so it must
#: change whenever this module's arithmetic does.
CONVERT_VERSION = "convert/1"

PHYSICAL_SOURCE = "internal"
PHYSICAL_SOURCE_ID = "intl-yard-and-pound-1959"
PHYSICAL_VERSION = "intl-yard-and-pound-1959"
PHYSICAL_RETRIEVED = "1959-07-01"
#: Likewise `license`: a 1959 treaty definition is not a dataset licence, and
#: `internal` is the vocabulary's slot for "foodcore's own assertion".
PHYSICAL_LICENSE = "internal"

PACKAGE_DEFAULT_SOURCE = "internal"

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
    #: WHICH provider declared it, from provenance.SOURCES. A package size can
    #: arrive from an Open Food Facts record, a USDA branded entry, or the user
    #: reading a tin in their kitchen — defaulting to "off" would attribute a
    #: hand-typed number to a dataset that never saw it, and a provenance lie is
    #: the one failure this module exists to prevent. `internal` means "foodcore
    #: is repeating what its caller declared", which is the truth when unset.
    source: str = "internal"


#: Licence follows the provider: OFF data carries ODbL's share-alike duty even
#: when it reaches us through a caller, USDA is CC0, and anything the caller
#: simply asserted is `internal`. Hardcoding one licence at the call site is how
#: an ODbL obligation silently goes missing.
_SOURCE_LICENSE = {"off": "ODbL-1.0", "usda": "CC0-1.0",
                   "foodon": "CC-BY-4.0", "internal": "internal"}


def _license_for(source: str) -> str:
    return _SOURCE_LICENSE.get(source, "internal")


def _prov(source: str, source_id: str, retrieved: str, method: str,
          version: str, confidence: float, lic: str) -> Provenance:
    return Provenance(
        source=source, source_id=source_id, retrieved=retrieved, method=method,
        version=version, confidence=confidence, license=lic,
    )


def _dataset_prov(dataset: Dataset, food: FoodRecord, row_id: str, method: str,
                  confidence: float) -> Provenance:
    """Cite the snapshot the portion row actually came from.

    Delegated to the dataset rather than assembled here: the provider, the
    retrieval date and the LICENCE must agree with the `source` row in the file,
    and an engine that assembles its own provenance is how CC0 ends up stamped
    on ODbL data. `version` is this module's rule version — the dataset's own
    edition travels in `SourceRecord.dataset_version`, which is where a reader
    looks it up.
    """
    return dataset.provenance_for(
        food.provider, f"{food.source_id}#{row_id}", method, confidence,
        CONVERT_VERSION,
    )


def _physical_prov(from_unit: str, to_unit: str) -> Provenance:
    return _prov(PHYSICAL_SOURCE, f"{PHYSICAL_SOURCE_ID}:{from_unit}->{to_unit}", PHYSICAL_RETRIEVED,
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
    if p is None or p.measure is None:
        return None
    ml = VOLUME_ML[p.measure] * p.amount
    if ml <= 0:
        return None
    return (p.gram_weight / ml, p.source_id, CONF_PORTION_DERIVED)


def _volume_to_grams(amount: float, unit: str, food: FoodRecord,
                     dataset: Dataset) -> tuple[float, Provenance] | Unresolved:
    direct = food.portion(unit)
    if direct is not None:
        return (amount * direct.grams_per_unit,
                _dataset_prov(dataset, food, direct.source_id,
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
            f"{food.provider} {food.source_id} ({food.description}) publishes no "
            f"volume portion, so {unit} cannot be weighed",
        )
    g_per_ml, pid, conf = derived
    return (amount * VOLUME_ML[unit] * g_per_ml,
            _dataset_prov(dataset, food, pid, "calculated", conf))


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
                _dataset_prov(dataset, food, p.source_id,
                              "estimated", CONF_COUNT_SIZED))

    p = food.portion(unit)
    if p is not None:
        return (amount * p.grams_per_unit,
                _dataset_prov(dataset, food, p.source_id,
                              "estimated", CONF_COUNT_SIZED))

    # No size asked for and no sizeless row: the dataset only knows small /
    # medium / large. Standing "medium" in is a CONVENTION, so it is reported as
    # estimated at the lowest count confidence, with the assumption spelled out
    # in the provenance id rather than hidden.
    medium = food.portion(unit, "medium")
    if medium is not None:
        return (amount * medium.grams_per_unit,
                _dataset_prov(dataset, food, f"{medium.source_id}?size=medium",
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
        f"{food.provider} {food.source_id} ({food.description}) publishes no "
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
            _dataset_prov(dataset, food, p.source_id, "estimated",
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
                f"{food.provider} {food.source_id} ({food.description}) publishes no "
                f"volume portion, so a mass cannot become {dst}",
            )
        g_per_ml, pid, conf = derived
        prov = _dataset_prov(dataset, food, pid, "calculated",
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
    declared_by = package.source or PACKAGE_DEFAULT_SOURCE
    # Date AND licence come from the same place, or neither does. Taking
    # `retrieved` from the dataset's snapshot while hardcoding `license` from
    # the table below produced a Provenance citing the file's retrieval date
    # under a licence the file does not claim — half-delegated provenance is
    # how an ODbL duty goes missing.
    snapshot_prov = (
        dataset.provenance_for(declared_by, pid, "declared",
                               CONF_PACKAGE_DECLARED, CONVERT_VERSION)
        if (dataset is not None and not package.retrieved
            and dataset.retrieved_for(declared_by))
        else None
    )
    if snapshot_prov is not None:
        dprov = snapshot_prov
        retrieved = dprov.retrieved
    else:
        retrieved = package.retrieved
        dprov = _prov(declared_by, pid, retrieved, "declared", CONVERT_VERSION,
                      CONF_PACKAGE_DECLARED, _license_for(declared_by))

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
                            _prov(declared_by, pid, retrieved, "calculated",
                                  package.label or "",
                                  min(CONF_PACKAGE_DECLARED, conf),
                                  dprov.license))
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


def _household_food(dataset: Dataset, food_text: str):
    """The food a cook's bare word conventionally means, with its rule note.

    Returns None when the word is not one of the conventions, or when the
    convention names a food this dataset does not contain — releases differ, and
    a missing row must degrade to "no default", never to an error.
    """
    from .rules.household_v1 import household_note, household_source_id

    source_id = household_source_id(food_text)
    if not source_id:
        return None
    record = dataset.food_by_source_id(source_id)
    if record is None:
        return None
    return record, household_note(food_text)


def _as_convention(outcome, note: str):
    """Re-stamp a resolved value as an inferred one.

    The WEIGHT is still measured; the IDENTIFICATION is a convention, and the
    chain is only as strong as its weakest link. Presenting "2 cups flour" with
    the same confidence as an explicitly-named food would be the quiet
    overclaim this library exists to avoid.
    """
    from .rules.household_v1 import CONFIDENCE, VERSION

    if not getattr(outcome, "resolved", False):
        return outcome
    prov = outcome.provenance
    return Resolved(
        outcome.value,
        outcome.unit,
        Provenance(
            source=prov.source,
            source_id=f"{prov.source_id} via {VERSION}",
            retrieved=prov.retrieved,
            method="inferred",
            version=f"{prov.version}+{VERSION}",
            confidence=min(prov.confidence, CONFIDENCE),
            license=prov.license,
        ),
    )


def convert_text(text: str, to_unit: str, *, dataset: Dataset | None = None,
                 package: PackageSize | None = None) -> Resolved | Unresolved:
    """Resolve a written quantity ('2 cups flour') into `to_unit`.

    Food resolution is the dataset's confidence-tiered matcher, and a LOW match
    refuses with the candidates rather than picking the first row — which is how
    '1 cup rice' stays unresolved while '1 cup cooked rice' resolves.

    **But only when the food actually matters.** Pounds to grams is the 1959
    yard-and-pound agreement, true of flour and feathers alike; millilitres to
    litres likewise. Resolving the food first made '2 lb flour' refuse as
    `ambiguous_food` against a real USDA import, where "flour" legitimately
    matches arrowroot, carob, millet and potato flour — a REGRESSION against a
    plain unit table, and invisible against a small fixture where nothing
    collides. So the definitional conversion is attempted first and the food is
    only consulted when the units cross dimensions.
    """
    parsed = parse_quantity(text)
    if parsed.amount is None:
        return Unresolved("no_quantity", f"no leading quantity found in {text!r}")

    unit = parsed.unit
    # Same-dimension conversions are physics, not food science. Try that before
    # touching the dataset — it also means these keep working with no dataset
    # loaded at all.
    if unit is not None and parsed.size is None and package is None:
        physical = convert(parsed.amount, unit, to_unit, food=None,
                           dataset=dataset)
        if getattr(physical, "resolved", False):
            return physical

    if dataset is None:
        return Unresolved(
            "no_dataset",
            "no reference dataset is loaded, so a food-specific conversion "
            "cannot be justified; load one or convert between like units",
        )

    food: FoodRecord | None = None
    if parsed.food_text:
        match = dataset.resolve_food(parsed.food_text)
        # Only when the catalogue could not decide: a bare cooking word
        # ("flour", "milk") is ambiguous as DATA and unambiguous as LANGUAGE.
        # Consulted strictly after the matcher, so an explicit "arrowroot
        # flour" resolves on its own merits and never reaches the convention.
        if not match.confident:
            household = _household_food(dataset, parsed.food_text)
            if household is not None:
                food, convention = household
                if unit is None:
                    unit = "piece"
                if unit is None:
                    return Unresolved("unknown_unit", f"no unit found in {text!r}")
                out = convert(parsed.amount, unit, to_unit, food=food,
                              dataset=dataset, size=parsed.size, package=package)
                return _as_convention(out, convention)
        if not match.candidates:
            return Unresolved(
                "unknown_food",
                f"no food in the reference dataset matches {parsed.food_text!r}",
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
