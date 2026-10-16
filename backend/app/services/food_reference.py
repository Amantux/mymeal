"""The foodcore seam: the ONLY module in myMeal that imports foodcore.

``units.to_grams`` used to answer "how much does a cup of this weigh?" from
``_DENSITY_G_PER_ML`` — 41 hand-written densities. foodcore ships a USDA-backed
reference dataset covering thousands of foods, where every answer carries its
provenance and an answer it cannot justify comes back as ``Unresolved`` rather
than a plausible number.

Everything in myMeal keeps calling ``units.to_grams``. This module is the one
place that knows foodcore exists, so the library can be swapped, upgraded or
removed without touching a caller. Only foodcore's **documented public API**
(``load_dataset``, ``Dataset.search_foods``/``portion_grams``,
``Resolved``/``Unresolved``, ``rule_version``) is used here; its internals are
explicitly not a contract.

Three rules this module exists to enforce
-----------------------------------------
1. **No dataset means today's behaviour, exactly.** ``MYMEAL_FOODCORE_DATASET``
   defaults to blank, and with it blank nothing here runs: no file is opened,
   no lookup happens, and ``units.to_grams`` is the function it has always
   been. That is why this is safe to ship before anyone has imported data.
2. **A refusal is never a crash and never a zero.** Every path returns grams or
   ``None``; ``None`` means "no weight from the reference data", which is the
   same thing ``to_grams`` has always returned when it could not convert, and
   every caller already handles it (the weight view simply omits the estimate).
   No exception from foodcore, sqlite or a corrupt file escapes this module.
3. **Guessing WHICH food was meant is not allowed.** A wrong identity is worse
   than no answer, because it produces a confident number. Resolution is
   confidence-tiered (see ``_resolve``) and refuses when the evidence is weak
   or the candidates disagree about the weight.

Deliberately NOT used: ``foodcore.convert.parse_quantity`` (myMeal's own parser
handles ranges, alternatives, "optional", unicode fractions and prefixes, which
foodcore's does not) and ``load_dataset()`` with no path — that would fall back
to ``$FOODCORE_DATASET`` or the bundled minimal dataset, so "no dataset
configured" would silently mean something different in production than in the
tests. The host names its own file or this module stands down.
"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache

log = logging.getLogger(__name__)

# myMeal's canonical volume unit -> the measure name(s) a foodcore dataset uses.
#
# Two spellings per unit on purpose. FDC spells a measure in three different
# places and foodcore's importer normalises them to one household word; which
# word it picks ("tablespoon" vs "tbsp") is foodcore's internal choice, not
# something its README promises. Asking for both costs one extra indexed lookup
# on a miss and survives that choice changing.
#
# Mass units are absent deliberately: g/kg/oz/lb are definitional physics that
# units.py already does exactly, and routing them through a dataset could only
# make them worse. Count units ("clove", "stick") are absent too — those are
# Stage 1's non-goal and still go to the learned-conversions table.
_MEASURES: dict[str, tuple[str, ...]] = {
    "ml": ("milliliter", "ml"),
    "l": ("liter", "l"),
    "tsp": ("teaspoon", "tsp"),
    "tbsp": ("tablespoon", "tbsp"),
    "fl oz": ("fluid ounce", "fl oz"),
    "cup": ("cup",),
    "pint": ("pint",),
    "quart": ("quart",),
    "gallon": ("gallon",),
}

#: How many dataset candidates to score. A full USDA import answers "onion"
#: with dozens of rows; the question is only ever "is there one obvious
#: winner", and that is visible in the first handful.
_CANDIDATE_LIMIT = 8

#: Evidence tiers, with wide gaps so a stronger KIND of match always beats a
#: pile of weaker ones (the estate's standard resolution shape). USDA
#: descriptions are comma-delimited from the general to the specific —
#: "Flour, wheat, all-purpose, enriched" — so the head segment is the strongest
#: signal short of an exact name.
_SCORE_EXACT_NAME = 100
_SCORE_HEAD_SEGMENT = 70
_SCORE_PREFIX = 50
_SCORE_CONTAINS = 30

#: The weakest evidence that may resolve at all. A name that merely CONTAINS
#: the word never qualifies: "Onion rings, breaded, frozen" contains "onion".
_MIN_SCORE = _SCORE_HEAD_SEGMENT

#: When several equally-matched foods all publish a weight for the measure,
#: they are only interchangeable if they agree. Within 10% the choice cannot
#: change what a cook does; beyond it we are choosing a number, not reading
#: one, so the conversion is refused. This is what makes "1 cup onions" honest:
#: USDA publishes 160 g chopped and 115 g sliced, a 39% spread, and picking one
#: silently is exactly the guess the density table used to make.
_AGREEMENT = 0.10

_lock = threading.Lock()
#: path -> Dataset, or None when that path could not be opened. Negative
#: entries are cached too: a missing file must cost one stat, not one per
#: ingredient line on a recipe render. Both settings are restart-required, so a
#: newly-built dataset is picked up on the next start (or via reset_cache()).
_datasets: dict[str, object] = {}
_warned = False


def reset_cache() -> None:
    """Drop the cached dataset handles. For tests, and for a CLI that rebuilds
    the file in the same process."""
    with _lock:
        _datasets.clear()
        _lookup_grams.cache_clear()


def _setting(cfg, name: str, default):
    # getattr, not cfg.values[...]: a caller may hand us any object that looks
    # like settings, and an absent key must degrade to the default rather than
    # raise on a render path.
    try:
        return getattr(cfg, name)
    except Exception:  # noqa: BLE001 - config lookup must never break a render
        return default


def dataset_path(settings=None) -> str:
    """The configured dataset path, or "" when foodcore is not in use."""
    from .ai.settings_access import resolved  # local: keeps imports light

    cfg = resolved(settings)
    if not _setting(cfg, "FOODCORE_ENABLED", True):
        return ""
    return str(_setting(cfg, "FOODCORE_DATASET", "") or "").strip()


def _dataset(settings=None):
    """The open dataset, or None when there is nothing configured/usable."""
    path = dataset_path(settings)
    if not path:
        return None
    with _lock:
        if path in _datasets:
            return _datasets[path]
    ds = None
    try:
        import foodcore

        # allow_minimal=False: we passed an explicit path, so the bundled
        # minimal dataset can never be what comes back — but saying so means a
        # future foodcore cannot quietly substitute it for a file we named.
        ds = foodcore.load_dataset(path, allow_minimal=False)
    except Exception as exc:  # noqa: BLE001 - a bad dataset must not break the app
        # Named, not echoed: foodcore's DatasetError messages are curated, but
        # the path can come from operator config, so only the class and the
        # path we already know are logged.
        log.warning(
            "foodcore dataset at %r could not be opened (%s); falling back to "
            "myMeal's built-in densities",
            path, type(exc).__name__,
        )
    with _lock:
        _datasets[path] = ds
    return ds


def _forms(canonical: str) -> frozenset[str]:
    """The canonical name and its plural spellings.

    myMeal canonicalises a food to the singular ("onion"); USDA describes the
    same food in the plural ("Onions, raw"). Without this, the strong evidence
    tiers silently never fire for a whole class of foods and every lookup falls
    through to the weakest one — which is then rejected, so a dataset would
    appear to be loaded and useless. Pluralising the QUESTION rather than
    singularising the data is deliberate: stripping a trailing 's' from
    dataset names would turn "molasses" into "molasse" and "oats" into "oat".
    """
    forms = {canonical, canonical + "s", canonical + "es"}
    if canonical.endswith("y"):
        forms.add(canonical[:-1] + "ies")
    return frozenset(forms)


def _score(forms: frozenset[str], name: str) -> int:
    name = (name or "").strip().lower()
    if not name:
        return 0
    if name in forms:
        return _SCORE_EXACT_NAME
    if name.split(",")[0].strip() in forms:
        return _SCORE_HEAD_SEGMENT
    if any(name.startswith(form) for form in forms):
        return _SCORE_PREFIX
    return _SCORE_CONTAINS


def _resolve(ds, canonical: str, measures: tuple[str, ...]) -> float | None:
    """Grams for one `measure` of `canonical`, or None if nothing qualifies.

    Confidence-tiered, in two stages, because both kinds of ambiguity produce a
    confidently wrong number:

    * **identity** — score the candidates by the KIND of evidence and keep only
      the strongest tier. A tier below ``_MIN_SCORE`` never resolves.
    * **value** — if that tier holds several foods, they must agree within
      ``_AGREEMENT`` to be interchangeable. If they don't, refuse.

    Ties are broken by shortest name, which in USDA's description grammar is
    the least-qualified (most generic) food — and it only ever picks between
    candidates that already agree about the weight.
    """
    import foodcore

    # The search stays on the singular canonical: it is a substring match, so
    # it already reaches "Onions, raw". Only the SCORING needs the plurals.
    candidates = ds.search_foods(canonical, _CANDIDATE_LIMIT)
    if not candidates:
        return None
    forms = _forms(canonical)
    scored = [(_score(forms, row.name), len(row.name or ""), row) for row in candidates]
    best = max(s for s, _, _ in scored)
    if best < _MIN_SCORE:
        return None

    version = foodcore.rule_version("convert")
    values: list[float] = []
    for _s, _len, row in sorted(
        (item for item in scored if item[0] == best), key=lambda i: (i[1], i[2].name)
    ):
        for measure in measures:
            outcome = ds.portion_grams(row.key, measure, version=version)
            if outcome.resolved:
                # A food in the winning tier that publishes no weight for this
                # measure carries no number, so it cannot disagree with one.
                # It is skipped, not treated as a conflict.
                if outcome.value > 0:
                    values.append(float(outcome.value))
                break

    if not values:
        return None
    if min(values) <= 0 or max(values) / min(values) > 1 + _AGREEMENT:
        return None
    return values[0]


#: Memoised per (dataset, food, measure). A recipe render converts every line
#: and a shopping list converts every recipe, so the same handful of foods is
#: asked for repeatedly; the dataset handle is cached per path, so its identity
#: is a stable cache key.
@lru_cache(maxsize=2048)
def _lookup_grams(ds, canonical: str, measures: tuple[str, ...]) -> float | None:
    return _resolve(ds, canonical, measures)


def grams_per(unit: str, food_text: str, settings=None) -> float | None:
    """Grams for ONE `unit` of `food_text`, from the reference dataset.

    Deliberately the same shape as the ``learned`` callable ``to_grams``
    already accepts — ``(unit, food) -> grams_per_unit | None`` — so the
    reference lookup slots in beside it instead of reshaping the caller.

    Returns None, never raises, whenever the answer is not justified: no
    dataset configured (the shipped default), no such food, no portion weight
    for that measure, weak identity evidence, or candidates that disagree.
    """
    global _warned
    try:
        measures = _MEASURES.get(unit)
        if not measures:
            return None
        ds = _dataset(settings)
        if ds is None:
            return None

        from . import food_resolve  # local: keeps this module import-light

        canonical = food_resolve.match_key(food_text)
        if not canonical:
            return None
        return _lookup_grams(ds, canonical.strip().lower(), measures)
    except Exception as exc:  # noqa: BLE001 - a lookup must never break a render
        if not _warned:
            _warned = True
            log.warning(
                "foodcore lookup failed (%s); myMeal's built-in densities will "
                "be used for the rest of this process", type(exc).__name__,
            )
        else:
            log.debug("foodcore lookup failed (%s)", type(exc).__name__)
        return None
