"""Per-serving nutrition for a recipe, by arithmetic where possible and by
model estimate where not.

Two ways a recipe can get a nutrition panel, and they are NOT equally good:

* ``compute_nutrition`` — ``(edible_grams / 100) x USDA per-100 g``, summed,
  divided by servings, by ``foodcore.nutrition`` via myMeal's ``food_reference``
  seam. Deterministic, reproducible, and every figure carries the mass coverage
  it was computed from. It answers ``None`` whenever the reference dataset
  cannot justify an answer — which is **always** on an install that has not
  imported one, i.e. most of them.
* ``estimate_nutrition`` — asks a text model. This is the path myMeal has
  always had and it stays, because the alternative for an install with no
  dataset is no nutrition at all. It is a guess, and it is now labelled one.

``nutrition_for_recipe`` composes them: arithmetic first, estimate second. The
result is stored on ``Recipe.nutrition`` as JSON and always carries a
``source``, so neither the UI nor a future reader has to guess which of the two
produced the number in front of them.

Why a field in the JSON rather than a column: ``Recipe.nutrition`` is already a
JSON blob of per-serving figures, the label belongs to the figures rather than
to the recipe, and a column would need a migration to say something the blob
can say for free. ``sanitize`` is what keeps the blob a closed vocabulary.

``0`` NO LONGER MEANS "DIDN'T KNOW"
-----------------------------------
The system prompt below used to say "use 0 for a field you cannot estimate",
so a stored ``0`` meant both *this recipe has no fibre* and *the model had no
idea*. It now says to OMIT such a field, and absence is carried explicitly end
to end: a computed result lists the fields it could not stand behind in
``unknown``, and the UI renders those as an absence rather than as a number.
Rows written before this change cannot be retro-corrected — a ``0`` already in
the database is unlabelled and stays ambiguous until the recipe is recomputed.
"""
import math

from .base import AIProvider

# Canonical per-serving fields. Units: calories = kcal, macros = grams,
# sodium = milligrams. These ids are deliberately the ones
# ``foodcore.nutrition.NUTRIENTS`` declares, so the arithmetic path needs no
# translation on the way out — the translation that IS needed, from the
# dataset's own key spelling, lives in ``food_reference._DATASET_NUTRIENTS``.
FIELDS = ("calories", "protein", "carbs", "fat", "fiber", "sugar", "sodium")

#: Where a stored panel's numbers came from. A closed vocabulary, because the
#: UI branches on it and an unrecognised value would render as a claim nobody
#: made.
SOURCE_COMPUTED = "computed"      # USDA arithmetic, reproducible
SOURCE_ESTIMATED = "estimated"    # a language model's guess
SOURCE_MANUAL = "manual"          # a human typed it
SOURCES = (SOURCE_COMPUTED, SOURCE_ESTIMATED, SOURCE_MANUAL)

#: Provenance keys a computed panel may carry. An allowlist rather than a
#: passthrough: this blob round-trips through the public update endpoint (a
#: version restore re-applies it), so anything not named here must not be
#: persistable by a client.
_PROVENANCE_KEYS = ("dataset", "retrieved", "method", "algorithm",
                    "minCompleteness", "unweighable")

_SYSTEM = (
    "You are a nutrition estimator. Given a recipe's ingredients and its serving "
    "count, estimate the nutrition PER SERVING from typical food values. Return "
    "ONLY JSON with numeric fields: calories (kcal); protein, carbs, fat, fiber, "
    "sugar (grams); sodium (milligrams). OMIT a field you cannot estimate — do "
    "NOT write 0 for it, because 0 means the recipe genuinely has none of it. "
    "No commentary."
)


def _clean_completeness(raw) -> dict:
    out = {}
    if not isinstance(raw, dict):
        return out
    for f in FIELDS:
        try:
            n = float(raw.get(f))
        except (TypeError, ValueError):
            continue
        if math.isfinite(n) and 0.0 <= n <= 1.0:
            out[f] = round(n, 3)
    return out


def _clean_provenance(raw) -> dict:
    out = {}
    if not isinstance(raw, dict):
        return out
    for key in _PROVENANCE_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:120]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isfinite(float(value)):
                out[key] = round(float(value), 3)
        elif isinstance(value, list):
            items = [str(v)[:120] for v in value[:20] if str(v).strip()]
            if items:
                out[key] = items
    return out


def sanitize(raw, *, source: str | None = None) -> dict:
    """Keep only the known fields, coerced to non-negative rounded numbers.

    ``source`` is minted by the SERVER — pass ``SOURCE_COMPUTED`` from the
    arithmetic path, ``SOURCE_ESTIMATED`` from the model path. When it is not
    given, a label and its metadata already present in ``raw`` are carried
    through if they are vocabulary-valid, which is what lets a version restore
    re-apply a computed panel without silently demoting it to an unlabelled
    one (the snapshot stores the whole blob, so it arrives back here intact).

    A hand edit from the UI carries no label, because the edit form posts only
    the numeric fields — so typing over a computed figure correctly stops
    claiming it was computed.
    """
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for f in FIELDS:
        try:
            n = round(float(raw.get(f)), 1)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(n) or n < 0:  # NaN, ±inf, or negative
            continue
        out[f] = n
    if not out:
        return out

    if source is not None:
        if source not in SOURCES:
            raise ValueError(f"unknown nutrition source {source!r}")
        out["source"] = source
        return out

    carried = raw.get("source")
    if carried in SOURCES:
        out["source"] = carried
        if carried == SOURCE_COMPUTED:
            # Coverage and provenance describe arithmetic. Carrying them onto
            # anything else would attach a measured pedigree to a guess.
            completeness = _clean_completeness(raw.get("completeness"))
            if completeness:
                out["completeness"] = completeness
            unknown = [f for f in FIELDS
                       if f in (raw.get("unknown") or []) and f not in out]
            if unknown:
                out["unknown"] = unknown
            provenance = _clean_provenance(raw.get("provenance"))
            if provenance:
                out["provenance"] = provenance
    return out


def _stored(result) -> dict | None:
    """Turn a ``foodcore.nutrition.RecipeNutrition`` into the stored blob.

    Reads ``NutrientTotal.value`` and never ``partial_value``: the partial sum
    is biased low by construction (every unresolved ingredient pushes it down
    and none push it up), so presenting it as the recipe's figure is the exact
    lie this stage exists to remove. A nutrient below foodcore's completeness
    floor goes into ``unknown`` instead, which the UI renders as an absence.

    Returns ``None`` when NOTHING cleared the floor. That is the dataset saying
    it cannot answer this recipe, so the caller falls back to an estimate
    rather than storing an all-unknown panel — on an install with no AI
    provider either, the recipe simply keeps no nutrition, exactly as today.
    """
    values: dict = {}
    completeness: dict = {}
    unknown: list = []
    provenance = None
    for f in FIELDS:
        total = result.per_serving.get(f)
        if total is None:
            unknown.append(f)
            continue
        if not total.resolved:
            unknown.append(f)
            continue
        values[f] = round(float(total.value), 1)
        completeness[f] = round(float(total.completeness), 3)
        if provenance is None:
            provenance = getattr(total.outcome, "provenance", None)
    if not values:
        return None

    out = sanitize(values, source=SOURCE_COMPUTED)
    out["completeness"] = completeness
    if unknown:
        out["unknown"] = unknown
    meta = {
        "algorithm": _algorithm_version(),
        "minCompleteness": round(float(result.min_completeness), 3),
    }
    if provenance is not None:
        meta["dataset"] = str(provenance.source)
        if provenance.retrieved:
            meta["retrieved"] = str(provenance.retrieved)
        meta["method"] = str(provenance.method)
    # The lines that had no weighable mass at all ("salt to taste"). Named, not
    # counted: "and 2 others" tells a cook nothing they can act on, whereas the
    # actual lines tell them which quantity to write down.
    if result.unweighable:
        meta["unweighable"] = list(result.unweighable)
    out["provenance"] = _clean_provenance(meta)
    return out


def _algorithm_version() -> str:
    try:
        import foodcore.nutrition as fcn

        return str(fcn.ALGORITHM_VERSION)
    except Exception:  # noqa: BLE001 - a version string must not break a save
        return "foodcore-nutrition"


def compute_nutrition(ingredient_lines: list[str], servings: int,
                      settings=None) -> dict | None:
    """USDA arithmetic for one recipe, or ``None`` when it cannot be justified.

    ``None`` covers every "the reference data cannot answer this" case and is
    the shipped default, because the dataset is operator-built and absent until
    someone imports one. No model is called and no network is touched.
    """
    from ..food_reference import recipe_nutrition

    result = recipe_nutrition(ingredient_lines, servings, nutrients=FIELDS,
                              settings=settings)
    return _stored(result) if result is not None else None


def estimate_nutrition(
    ingredient_lines: list[str], servings: int, provider: AIProvider
) -> dict:
    lines = "\n".join(f"- {ln}" for ln in ingredient_lines if str(ln).strip())
    prompt = (
        f"Servings: {servings or 'unknown (assume 4)'}\n"
        f"Ingredients:\n{lines}\n\n"
        'Return JSON like {"calories":520,"protein":28,"carbs":45,"fat":22,'
        '"fiber":6,"sugar":8,"sodium":640}.'
    )
    return sanitize(
        provider.complete_json(prompt, system=_SYSTEM, max_tokens=512),
        source=SOURCE_ESTIMATED,
    )


def nutrition_for_recipe(ingredient_lines: list[str], servings: int,
                         provider: AIProvider | None, settings=None) -> dict:
    """Arithmetic if the reference data can answer, otherwise a model estimate.

    In front of, not instead of: an install with no dataset gets exactly the
    estimate it gets today, from the same prompt and the same provider, and an
    install with one stops asking a text model to do multiplication.

    ``provider`` may be ``None`` — with a dataset that answers, no provider is
    needed at all, which is why the callers no longer refuse up front.
    """
    computed = compute_nutrition(ingredient_lines, servings, settings=settings)
    if computed:
        return computed
    if provider is None:
        return {}
    return estimate_nutrition(ingredient_lines, servings, provider)
