"""Deterministic recipe nutrition arithmetic — the spec's FR-05.

    ingredient_nutrient = (edible_grams / 100) x nutrient_per_100g
    recipe_nutrient     = sum(ingredient_nutrient)
    serving_nutrient    = recipe_nutrient / servings

This module replaces an LLM estimate (``mymeal/backend/app/services/ai/
nutrition.py``, which asks a text model for the macros and tells it to "use 0
for a field you cannot estimate"). It is a pure module: no Flask, no database,
no network, no model calls, ever. The LLM's only legitimate role in this
pipeline is deciding WHICH food an ingredient line means; that happens upstream
and is not this code's business.

----------------------------------------------------------------------------
DESIGN DECISION 1 — "missing is not zero", and how a partial total is presented
----------------------------------------------------------------------------
The module it replaces makes ``0`` mean both *zero* and *didn't know*. The fix
has two halves, and both matter:

*   **Unknowns are never summed as zero.** An ingredient with no composition
    record, with unresolvable grams, or whose record simply omits the nutrient
    (USDA records very often carry kcal but no added sugar) contributes
    *nothing to the numerator and nothing to the coverage*. The honest total of
    "chicken + rice + ???" is not "chicken + rice".

*   **Every total carries per-nutrient completeness, and below a threshold it
    is refused outright.** ``completeness`` is the fraction of the recipe's
    *weighable mass* covered by ingredients that produced a value for that
    nutrient. Above ``min_completeness`` (default 0.80) the caller gets a
    number plus the completeness figure; below it the caller gets
    ``Unresolved``.

Three sub-decisions, because each has a plausible alternative:

1.  *Why mass-weighted, not ingredient-count-weighted?* Count is the intuitive
    measure and the wrong one: "8 of 11 ingredients" treats a missing 400 g
    chicken breast and a missing pinch of oregano as equal errors. Mass is what
    actually drives the arithmetic, so mass is what completeness must measure.

2.  *Why per-nutrient, not per-recipe?* Coverage is not uniform across
    nutrients. A recipe can be 100% covered for calories and 55% covered for
    fibre from the very same records. One recipe-level figure would have to be
    the minimum (uselessly pessimistic for calories) or the mean (actively
    misleading for fibre).

3.  *Why refuse below a threshold instead of always returning a caveated
    number?* Because a partial total is not noisy, it is **biased low** — every
    unknown pushes it down and none push it up — and users read a number as the
    answer no matter what sits beside it. A calorie figure that is 45% of the
    truth is worse than "unknown", because it silently licenses a decision.
    Above the threshold the bias is bounded by the uncovered mass, which is
    reported, so a number plus completeness is strictly more useful than
    nothing. The threshold is a parameter, not a constant, because the right
    cut-off for "show the user a calorie count" and for "sum a week of plans"
    differ — but it has a default so refusing is the easy path.

A line whose *grams* could not be resolved at all ("salt to taste") cannot be
folded into a mass fraction — its mass is by definition unknown, so it has no
denominator. Those lines are therefore reported separately in
``RecipeNutrition.unweighable``, flip ``mass_complete`` to False, and apply a
confidence penalty. They are never silently dropped. If *no* line is weighable
there is no recipe mass at all and every nutrient is ``Unresolved``.

----------------------------------------------------------------------------
DESIGN DECISION 2 — how uncertainty propagates
----------------------------------------------------------------------------
Nutrition derived from estimated grams ("3 medium onions") must not claim to be
``measured``. Two independent rules:

*   **Method = the weakest link, capped at ``calculated``.** The output method
    is ``min(calculated, grams.method, composition.method, factor.method)`` on
    the lattice ``estimated < inferred < calculated < declared < measured``.
    Minimum, because a chain is only as strong as its weakest input. Capped at
    ``calculated``, because even measured grams times a measured composition
    yields a number that was *computed*, never measured — which is exactly what
    the spec's vocabulary is for.

*   **Confidence of one ingredient = the PRODUCT of its inputs' confidences.**
    Product, not minimum, because the inputs are a *chain* of independent
    opportunities to be wrong (the grams can be wrong AND the food match can be
    wrong AND the retention factor can be wrong); independent error
    probabilities compound. Minimum would be right if the inputs were competing
    *alternatives* for the same fact, which they are not.

*   **Confidence of a total = the mass-weighted mean of its contributors'
    confidences**, times the fraction of lines that were weighable. Mean, not
    product, because here the ingredients *are* parallel alternatives
    contributing to one sum — a hundred well-sourced ingredients should not
    drive confidence to zero the way a product would. Mass-weighted for the
    same reason completeness is. Completeness is deliberately NOT folded into
    confidence: it is reported separately and folding it in would double-count
    it and destroy the caller's ability to tell "few ingredients, well known"
    from "all ingredients, poorly known".

*   ``retrieved`` on a derived value is the OLDEST of its inputs (a total is
    only as fresh as its stalest ingredient) and ``license`` is the
    ``AND``-join of every input licence, because licence obligations compose
    conjunctively.

----------------------------------------------------------------------------
DESIGN DECISION 3 — raw vs cooked
----------------------------------------------------------------------------
Applying raw composition to a cooked food is a factor-of-three error for
anything that absorbs water (raw rice is 365 kcal/100 g, cooked is 130), so:

*   A composition record declares the ``basis`` it was measured on and a recipe
    line declares the basis it ``used_as``. If they differ concretely, the line
    is ``Unresolved("basis_mismatch")`` — it does NOT quietly use the wrong
    record. Pick the better-matching record upstream instead.
*   A **sourced** ``ConversionFactor`` (yield ratio + per-nutrient retention,
    with its own provenance) bridges the gap when one is supplied. Its
    confidence multiplies into the result and its method joins the
    weakest-link calculation. Unsourced factors are not invented here — there
    is no default yield table in this module, on purpose.
*   If exactly one side says ``unspecified`` the record is used as-is but the
    method is additionally capped at ``inferred``, because we inferred a basis
    match rather than knowing one. That keeps legacy data usable while marking
    it honestly.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from .provenance import Provenance, Resolved, Unresolved

ALGORITHM_VERSION = "foodcore-nutrition/1"

#: Default mass-coverage below which a total is refused rather than reported.
DEFAULT_MIN_COMPLETENESS = 0.80

# --------------------------------------------------------------------------
# Method lattice (weakest first). Anything not in here is treated as the
# weakest value, so an unrecognised method can never upgrade a result.
# --------------------------------------------------------------------------
METHODS: tuple[str, ...] = ("estimated", "inferred", "calculated", "declared", "measured")
_METHOD_RANK = {m: i for i, m in enumerate(METHODS)}
METHOD_CALCULATED = "calculated"
METHOD_INFERRED = "inferred"


def weakest_method(*methods: str) -> str:
    """Weakest method on the lattice. Unknown values rank weakest of all."""
    rank = min(_METHOD_RANK.get(m, 0) for m in methods) if methods else 0
    return METHODS[rank]


# --------------------------------------------------------------------------
# Preparation basis
# --------------------------------------------------------------------------
BASIS_RAW = "raw"
BASIS_COOKED = "cooked"
BASIS_AS_PURCHASED = "as_purchased"
BASIS_UNSPECIFIED = "unspecified"
BASES = (BASIS_RAW, BASIS_COOKED, BASIS_AS_PURCHASED, BASIS_UNSPECIFIED)


# --------------------------------------------------------------------------
# Nutrient registry
#
# Requirement: do NOT hardcode myMeal's current seven fields as the universe.
# So the registry is a *default catalogue*, not a gate: any nutrient id present
# in a composition record is totalled, registered or not. The registry only
# supplies a label and the canonical reporting unit. `register_nutrient` exists
# for callers who want their own ids reported in their own canonical unit.
# --------------------------------------------------------------------------
DIMENSION_MASS = "mass"
DIMENSION_ENERGY = "energy"

#: Factors to the dimension's base unit (g for mass, kcal for energy).
_UNIT_FACTORS: dict[str, tuple[str, float]] = {
    "kg": (DIMENSION_MASS, 1000.0),
    "g": (DIMENSION_MASS, 1.0),
    "mg": (DIMENSION_MASS, 1e-3),
    "ug": (DIMENSION_MASS, 1e-6),
    "mcg": (DIMENSION_MASS, 1e-6),
    "µg": (DIMENSION_MASS, 1e-6),
    "kcal": (DIMENSION_ENERGY, 1.0),
    "kj": (DIMENSION_ENERGY, 1.0 / 4.184),
    # "cal" is deliberately absent: it means kcal in food labelling and
    # 1/1000 kcal in physics. Refusing is better than picking.
}


@dataclass(frozen=True)
class Nutrient:
    """Declaration of a nutrient this module knows how to report."""

    id: str
    label: str
    unit: str

    @property
    def dimension(self) -> str:
        return _UNIT_FACTORS[normalize_unit(self.unit)][0]


def _n(nid: str, label: str, unit: str) -> Nutrient:
    return Nutrient(id=nid, label=label, unit=unit)


NUTRIENTS: dict[str, Nutrient] = {
    n.id: n
    for n in (
        # The eight the task requires.
        _n("calories", "Energy", "kcal"),
        _n("protein", "Protein", "g"),
        _n("carbs", "Carbohydrate", "g"),
        _n("fat", "Total fat", "g"),
        _n("fiber", "Dietary fibre", "g"),
        _n("sugar", "Total sugars", "g"),
        _n("saturated_fat", "Saturated fat", "g"),
        _n("sodium", "Sodium", "mg"),
        # A starter set of vitamins and minerals — extend via register_nutrient
        # or just put the id in a composition record.
        _n("trans_fat", "Trans fat", "g"),
        _n("cholesterol", "Cholesterol", "mg"),
        _n("calcium", "Calcium", "mg"),
        _n("iron", "Iron", "mg"),
        _n("magnesium", "Magnesium", "mg"),
        _n("potassium", "Potassium", "mg"),
        _n("zinc", "Zinc", "mg"),
        _n("vitamin_a_rae", "Vitamin A (RAE)", "µg"),
        _n("vitamin_c", "Vitamin C", "mg"),
        _n("vitamin_d", "Vitamin D", "µg"),
        _n("vitamin_e", "Vitamin E", "mg"),
        _n("vitamin_k", "Vitamin K", "µg"),
        _n("thiamin", "Thiamin (B1)", "mg"),
        _n("riboflavin", "Riboflavin (B2)", "mg"),
        _n("niacin", "Niacin (B3)", "mg"),
        _n("vitamin_b6", "Vitamin B6", "mg"),
        _n("folate", "Folate (DFE)", "µg"),
        _n("vitamin_b12", "Vitamin B12", "µg"),
    )
}


def register_nutrient(nutrient: Nutrient) -> None:
    """Add or replace a nutrient declaration. Raises on an unknown unit."""
    if not known_unit(nutrient.unit):
        raise ValueError(f"unknown nutrient unit {nutrient.unit!r}")
    NUTRIENTS[nutrient.id] = nutrient


def normalize_unit(unit: str) -> str:
    """Case-fold a nutrient unit for lookup (``kJ`` -> ``kj``, ``MG`` -> ``mg``)."""
    return str(unit).strip().lower()


def known_unit(unit: str) -> bool:
    return normalize_unit(unit) in _UNIT_FACTORS


def _convert(amount: float, from_unit: str, to_unit: str) -> float | None:
    """Convert within one dimension. ``None`` when not commensurable.

    This is a deliberately tiny table covering only the units nutrient values
    come in. General quantity conversion is ``convert.py``'s job, not ours.
    """
    src = _UNIT_FACTORS.get(normalize_unit(from_unit))
    dst = _UNIT_FACTORS.get(normalize_unit(to_unit))
    if src is None or dst is None or src[0] != dst[0]:
        return None
    return amount * src[1] / dst[1]


# --------------------------------------------------------------------------
# Inputs
#
# These are narrow and explicit on purpose. Nothing here accepts a loose dict,
# and the two places data can be absent (grams, composition) are typed as
# `Resolved | Unresolved` — so a caller with partial data cannot just pass
# `None` or omit a key; it has to state a reason.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class NutrientAmount:
    """One nutrient in a composition record, per 100 g of that food."""

    amount: float
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, (int, float)) or not math.isfinite(self.amount):
            raise ValueError(f"nutrient amount must be a finite number, got {self.amount!r}")
        if not known_unit(self.unit):
            raise ValueError(f"unknown nutrient unit {self.unit!r}")


@dataclass(frozen=True)
class Composition:
    """A per-100 g nutrient record for ONE food, exactly as sourced.

    ``per_100g`` holds only the nutrients the source actually reports. An
    absent key means "this source does not say", which is NOT zero — a source
    that genuinely measured zero fibre reports ``NutrientAmount(0.0, "g")``.
    Preserving that distinction is the whole point of this module.
    """

    food_id: str
    description: str
    basis: str
    per_100g: Mapping[str, NutrientAmount]
    provenance: Provenance

    def __post_init__(self) -> None:
        if self.basis not in BASES:
            raise ValueError(f"basis must be one of {BASES}, got {self.basis!r}")
        for key, value in self.per_100g.items():
            if not isinstance(value, NutrientAmount):
                raise TypeError(f"per_100g[{key!r}] must be a NutrientAmount")
        object.__setattr__(self, "per_100g", dict(self.per_100g))


@dataclass(frozen=True)
class ConversionFactor:
    """A **sourced** bridge between a composition's basis and the recipe's.

    ``yield_ratio`` = grams of ``to_basis`` food produced per gram of
    ``from_basis`` food (raw -> cooked rice is roughly 2.8). ``retention`` is
    the fraction of each nutrient surviving the transformation, keyed by
    nutrient id; ``default_retention`` applies to nutrients not listed (1.0 is
    right for macros, which are conserved).

    There is no default factor table in this module. Without a factor a basis
    mismatch is refused, because inventing a yield is exactly the fabrication
    the spec's unresolved-conversion rule forbids.
    """

    from_basis: str
    to_basis: str
    yield_ratio: float
    provenance: Provenance
    retention: Mapping[str, float] = ()  # type: ignore[assignment]
    default_retention: float = 1.0

    def __post_init__(self) -> None:
        if self.from_basis not in BASES or self.to_basis not in BASES:
            raise ValueError("conversion basis must be one of " + repr(BASES))
        if not math.isfinite(self.yield_ratio) or self.yield_ratio <= 0:
            raise ValueError("yield_ratio must be a positive finite number")
        object.__setattr__(self, "retention", dict(self.retention or {}))

    def retention_for(self, nutrient: str) -> float:
        return float(self.retention.get(nutrient, self.default_retention))


@dataclass(frozen=True)
class RecipeLine:
    """One ingredient line, already resolved upstream.

    ``edible_grams`` comes from the quantity converter (``Resolved`` in grams,
    or ``Unresolved`` with a reason — "1 can tomatoes" with no portion weight).
    ``composition`` comes from the food-data source (``Resolved`` is not the
    right shape for a whole record, so it is ``Composition | Unresolved``).
    """

    label: str
    edible_grams: Resolved | Unresolved
    composition: Composition | Unresolved
    used_as: str = BASIS_UNSPECIFIED
    conversion: ConversionFactor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.edible_grams, (Resolved, Unresolved)):
            raise TypeError(
                "edible_grams must be Resolved or Unresolved — state why it is "
                "missing rather than passing None"
            )
        if not isinstance(self.composition, (Composition, Unresolved)):
            raise TypeError(
                "composition must be a Composition or Unresolved — state why it "
                "is missing rather than passing None"
            )
        if self.used_as not in BASES:
            raise ValueError(f"used_as must be one of {BASES}, got {self.used_as!r}")


@dataclass(frozen=True)
class RecipeInput:
    """A recipe reduced to exactly what the arithmetic needs."""

    label: str
    servings: int | None
    lines: Sequence[RecipeLine]

    def __post_init__(self) -> None:
        for line in self.lines:
            if not isinstance(line, RecipeLine):
                raise TypeError("lines must all be RecipeLine instances")
        object.__setattr__(self, "lines", tuple(self.lines))


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class LineOutcome:
    """Per-ingredient diagnostics. ``blocked`` is a whole-line failure."""

    index: int
    label: str
    grams: float | None
    values: Mapping[str, Resolved]
    unresolved: Mapping[str, Unresolved]
    blocked: Unresolved | None = None

    @property
    def weighable(self) -> bool:
        return self.grams is not None


@dataclass(frozen=True)
class NutrientTotal:
    """A total, and everything needed to judge whether to believe it.

    ``outcome`` is the authority: ``Resolved`` when coverage reached the
    threshold, ``Unresolved`` otherwise. ``partial_value`` is the sum over the
    contributors only — it is biased low by construction and must never be
    presented to a user as the recipe's value; it exists for diagnostics and
    for a caller that wants to show "at least X".
    """

    nutrient: str
    unit: str
    outcome: Resolved | Unresolved
    completeness: float
    covered_grams: float
    weighable_grams: float
    contributors: tuple[str, ...]
    missing: tuple[str, ...]
    partial_value: float | None = None

    @property
    def resolved(self) -> bool:
        return isinstance(self.outcome, Resolved)

    @property
    def value(self) -> float | None:
        """The trustworthy value, or ``None``. Never the partial sum."""
        return self.outcome.value if isinstance(self.outcome, Resolved) else None


@dataclass(frozen=True)
class RecipeNutrition:
    label: str
    servings: int | None
    weighable_grams: float
    mass_complete: bool
    unweighable: tuple[str, ...]
    per_recipe: Mapping[str, NutrientTotal]
    per_serving: Mapping[str, NutrientTotal]
    lines: tuple[LineOutcome, ...]
    min_completeness: float


# --------------------------------------------------------------------------
# Arithmetic
# --------------------------------------------------------------------------
_GRAM_UNITS = {"g", "gram", "grams"}


def _grams_of(line: RecipeLine) -> tuple[float | None, Unresolved | None]:
    if isinstance(line.edible_grams, Unresolved):
        return None, line.edible_grams
    unit = line.edible_grams.unit.strip().lower()
    if unit not in _GRAM_UNITS:
        return None, Unresolved(
            reason="grams_unit",
            detail=f"{line.label}: edible_grams must be in grams, got {line.edible_grams.unit!r}",
        )
    value = line.edible_grams.value
    if not math.isfinite(value) or value < 0:
        return None, Unresolved(
            reason="grams_invalid",
            detail=f"{line.label}: edible grams must be finite and non-negative, got {value!r}",
        )
    return float(value), None


def _bridge(line: RecipeLine, comp: Composition) -> tuple[float, str, Unresolved | None]:
    """Resolve the basis gap.

    Returns ``(mass_scale, method_cap, unresolved)`` where ``mass_scale``
    converts grams-as-used into grams-on-the-composition's-basis.
    """
    used, measured = line.used_as, comp.basis
    factor = line.conversion

    if factor is not None:
        if factor.from_basis != measured or factor.to_basis != used:
            return 0.0, METHOD_CALCULATED, Unresolved(
                reason="factor_mismatch",
                detail=(
                    f"{line.label}: factor bridges {factor.from_basis}->{factor.to_basis} "
                    f"but the record is {measured} and the recipe uses it {used}"
                ),
            )
        # grams_used = grams_measured_basis * yield_ratio, so invert.
        return 1.0 / factor.yield_ratio, METHOD_CALCULATED, None

    if used == measured:
        return 1.0, METHOD_CALCULATED, None
    if used == BASIS_UNSPECIFIED or measured == BASIS_UNSPECIFIED:
        # Usable, but we inferred the match rather than knowing it.
        return 1.0, METHOD_INFERRED, None
    return 0.0, METHOD_CALCULATED, Unresolved(
        reason="basis_mismatch",
        detail=(
            f"{line.label}: composition is {measured} ({comp.description}) but the recipe "
            f"uses it {used}, and no sourced yield/retention factor was supplied"
        ),
    )


def _derive(
    value: float,
    unit: str,
    method_cap: str,
    sources: Sequence[Provenance],
    *,
    source: str | None = None,
    source_id: str | None = None,
) -> Resolved:
    """Build a ``Resolved`` whose provenance is the weakest-link combination."""
    method = weakest_method(method_cap, *(p.method for p in sources))
    confidence = 1.0
    for p in sources:
        confidence *= _clamp01(p.confidence)
    retrieved = min((p.retrieved for p in sources), default="")
    # Licences also arrive already AND-joined from a previous derivation step;
    # split them back apart so the set stays a set.
    licenses = sorted(
        {
            part.strip()
            for p in sources
            for part in p.license.split(" AND ")
            if part.strip()
        }
    )
    # Dataset versions accumulate as a set; our own algorithm version is
    # appended exactly once however many derivation steps have happened.
    versions = sorted(
        {
            part
            for p in sources
            for part in p.version.split("+")
            if part and part != ALGORITHM_VERSION
        }
    )
    primary = sources[0] if sources else None
    return Resolved(
        value=_round(value),
        unit=unit,
        provenance=Provenance(
            source=source if source is not None else (primary.source if primary else "foodcore"),
            source_id=source_id
            if source_id is not None
            else (primary.source_id if primary else ""),
            retrieved=retrieved,
            method=method,
            version="+".join([*versions, ALGORITHM_VERSION]),
            confidence=_round(confidence),
            license=" AND ".join(licenses),
        ),
    )


def _clamp01(x: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return min(1.0, max(0.0, v))


def _round(x: float) -> float:
    """Kill float noise so output is byte-stable for a given input."""
    return round(float(x), 9)


def evaluate_line(
    line: RecipeLine, index: int = 0, nutrients: Iterable[str] | None = None
) -> LineOutcome:
    """Compute every nutrient this one ingredient contributes, in absolute terms.

    ``ingredient_nutrient = (edible_grams / 100) x nutrient_per_100g``, with the
    basis bridge and retention applied. A whole-line failure (no grams, no
    composition, unbridgeable basis) sets ``blocked`` and yields no values —
    never zeros.
    """
    grams, grams_problem = _grams_of(line)

    if isinstance(line.composition, Unresolved):
        return LineOutcome(index, line.label, grams, {}, {}, blocked=line.composition)
    comp = line.composition

    if grams is None:
        assert grams_problem is not None
        return LineOutcome(index, line.label, None, {}, {}, blocked=grams_problem)

    scale, method_cap, problem = _bridge(line, comp)
    if problem is not None:
        return LineOutcome(index, line.label, grams, {}, {}, blocked=problem)

    assert isinstance(line.edible_grams, Resolved)
    wanted = set(nutrients) if nutrients is not None else set(comp.per_100g)
    values: dict[str, Resolved] = {}
    unresolved: dict[str, Unresolved] = {}
    basis_grams = grams * scale

    for nid in sorted(wanted):
        amount = comp.per_100g.get(nid)
        if amount is None:
            # The source does not report it. That is not zero.
            unresolved[nid] = Unresolved(
                reason="nutrient_not_reported",
                detail=f"{line.label}: {comp.provenance.source}:{comp.food_id} reports no {nid}",
            )
            continue
        retention = 1.0
        chain = [line.edible_grams.provenance, comp.provenance]
        if line.conversion is not None:
            retention = line.conversion.retention_for(nid)
            if not math.isfinite(retention) or retention < 0:
                unresolved[nid] = Unresolved(
                    reason="retention_invalid",
                    detail=f"{line.label}: retention for {nid} is {retention!r}",
                )
                continue
            chain.append(line.conversion.provenance)
        target_unit = NUTRIENTS[nid].unit if nid in NUTRIENTS else amount.unit
        converted = _convert(amount.amount, amount.unit, target_unit)
        if converted is None:
            unresolved[nid] = Unresolved(
                reason="unit_conflict",
                detail=f"{line.label}: cannot express {nid} in {amount.unit!r} as {target_unit!r}",
            )
            continue
        values[nid] = _derive(
            (basis_grams / 100.0) * converted * retention,
            target_unit,
            method_cap,
            chain,
        )

    return LineOutcome(index, line.label, grams, values, unresolved)


def nutrient_ids(recipe: RecipeInput) -> tuple[str, ...]:
    """Every nutrient any of the recipe's composition records reports."""
    seen: set[str] = set()
    for line in recipe.lines:
        if isinstance(line.composition, Composition):
            seen.update(line.composition.per_100g)
    return tuple(sorted(seen))


def compute_recipe(
    recipe: RecipeInput,
    nutrients: Iterable[str] | None = None,
    min_completeness: float = DEFAULT_MIN_COMPLETENESS,
) -> RecipeNutrition:
    """Total a recipe and divide by servings, honestly.

    ``nutrients`` defaults to the union of what the records report (so the
    caller is not limited to any fixed list of fields). ``min_completeness`` is
    the mass-coverage below which a total is ``Unresolved`` instead of a
    number — see DESIGN DECISION 1.
    """
    wanted = tuple(nutrients) if nutrients is not None else nutrient_ids(recipe)
    threshold = _clamp01(min_completeness)

    outcomes = tuple(
        evaluate_line(line, i, wanted) for i, line in enumerate(recipe.lines)
    )
    weighable = [o for o in outcomes if o.weighable]
    unweighable = tuple(o.label for o in outcomes if not o.weighable)
    weighable_grams = _round(sum(o.grams or 0.0 for o in weighable))
    mass_complete = not unweighable
    # The only denominator available for unquantified-mass lines is a count.
    line_fraction = (len(weighable) / len(outcomes)) if outcomes else 0.0

    per_recipe: dict[str, NutrientTotal] = {}
    for nid in wanted:
        per_recipe[nid] = _total(
            nid,
            outcomes,
            weighable_grams=weighable_grams,
            threshold=threshold,
            line_fraction=line_fraction,
            unweighable=unweighable,
        )

    per_serving = {
        nid: _per_serving(total, recipe.servings) for nid, total in per_recipe.items()
    }

    return RecipeNutrition(
        label=recipe.label,
        servings=recipe.servings,
        weighable_grams=weighable_grams,
        mass_complete=mass_complete,
        unweighable=unweighable,
        per_recipe=per_recipe,
        per_serving=per_serving,
        lines=outcomes,
        min_completeness=threshold,
    )


def _total(
    nid: str,
    outcomes: Sequence[LineOutcome],
    *,
    weighable_grams: float,
    threshold: float,
    line_fraction: float,
    unweighable: Sequence[str],
) -> NutrientTotal:
    contributors = [o for o in outcomes if nid in o.values]
    missing = tuple(o.label for o in outcomes if nid not in o.values)
    unit = NUTRIENTS[nid].unit if nid in NUTRIENTS else (
        contributors[0].values[nid].unit if contributors else ""
    )

    if weighable_grams <= 0:
        return NutrientTotal(
            nutrient=nid,
            unit=unit,
            outcome=Unresolved(
                reason="no_weighable_mass",
                detail="no ingredient line resolved to a gram weight, so the recipe has no mass",
            ),
            completeness=0.0,
            covered_grams=0.0,
            weighable_grams=weighable_grams,
            contributors=(),
            missing=missing,
        )

    units = {o.values[nid].unit for o in contributors}
    if len(units) > 1:
        return NutrientTotal(
            nutrient=nid,
            unit=unit,
            outcome=Unresolved(
                reason="unit_conflict",
                detail=f"{nid} arrived in incompatible units {sorted(units)}",
            ),
            completeness=0.0,
            covered_grams=0.0,
            weighable_grams=weighable_grams,
            contributors=tuple(o.label for o in contributors),
            missing=missing,
        )

    covered_grams = _round(sum(o.grams or 0.0 for o in contributors))
    completeness = _round(covered_grams / weighable_grams)
    partial = _round(sum(o.values[nid].value for o in contributors)) if contributors else None

    if not contributors or completeness < threshold:
        detail = (
            f"{nid}: only {completeness * 100:.1f}% of the recipe's {weighable_grams:g} g "
            f"has a value (threshold {threshold * 100:.0f}%)"
        )
        if unweighable:
            detail += f"; {len(unweighable)} line(s) have no weight at all: {', '.join(unweighable)}"
        return NutrientTotal(
            nutrient=nid,
            unit=unit,
            outcome=Unresolved(reason="incomplete", detail=detail),
            completeness=completeness,
            covered_grams=covered_grams,
            weighable_grams=weighable_grams,
            contributors=tuple(o.label for o in contributors),
            missing=missing,
            partial_value=partial,
        )

    provs = [o.values[nid].provenance for o in contributors]
    weights = [o.grams or 0.0 for o in contributors]
    total_weight = sum(weights)
    if total_weight > 0:
        mean_conf = sum(
            _clamp01(p.confidence) * w for p, w in zip(provs, weights, strict=True)
        ) / total_weight
    else:
        mean_conf = sum(_clamp01(p.confidence) for p in provs) / len(provs)
    aggregate_conf = mean_conf * line_fraction

    source_id = "calc(" + ",".join(
        sorted({f"{p.source}:{p.source_id}" for p in provs})
    ) + ")"
    resolved = _derive(
        sum(o.values[nid].value for o in contributors),
        unit,
        METHOD_CALCULATED,
        provs,
        # A summed total came from no single provider, so `internal` — the
        # vocabulary's slot for foodcore's own derivations — is the honest
        # value. Nothing is lost: source_id above is the sorted composite of
        # every contributing "<source>:<id>", so the inputs stay traceable, and
        # `version` already names the module that did the arithmetic.
        source="internal",
        source_id=source_id,
    )
    resolved = replace(
        resolved,
        provenance=replace(resolved.provenance, confidence=_round(aggregate_conf)),
    )
    return NutrientTotal(
        nutrient=nid,
        unit=unit,
        outcome=resolved,
        completeness=completeness,
        covered_grams=covered_grams,
        weighable_grams=weighable_grams,
        contributors=tuple(o.label for o in contributors),
        missing=missing,
        partial_value=partial,
    )


def _per_serving(total: NutrientTotal, servings: int | None) -> NutrientTotal:
    """``recipe_nutrient / servings`` — or ``Unresolved``, never a guess.

    The module this replaces prompts the model with "unknown (assume 4)". A
    serving count is a fact about the recipe; if it is missing, inventing one
    turns an unknown into a wrong number that looks right.
    """
    usable = (
        isinstance(servings, int) and not isinstance(servings, bool) and servings > 0
    )
    if not usable:
        return replace(
            total,
            outcome=Unresolved(
                reason="servings_unknown",
                detail=(
                    f"servings is {servings!r}; a per-serving figure cannot be derived "
                    "and a serving count will not be invented"
                ),
            ),
            partial_value=None,
        )
    if isinstance(total.outcome, Unresolved):
        return replace(total, partial_value=_divide(total.partial_value, servings))
    scaled = replace(total.outcome, value=_round(total.outcome.value / servings))
    return replace(total, outcome=scaled, partial_value=_divide(total.partial_value, servings))


def _divide(value: float | None, servings: int) -> float | None:
    return None if value is None else _round(value / servings)
