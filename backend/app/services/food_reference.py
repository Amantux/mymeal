"""The foodcore seam: the ONLY module in myMeal that imports foodcore.

``units.to_grams`` used to answer "how much does a cup of this weigh?" from
``_DENSITY_G_PER_ML`` — 41 hand-written densities. foodcore ships a USDA-backed
reference dataset covering thousands of foods, where every answer carries its
provenance and an answer it cannot justify comes back as ``Unresolved`` rather
than a plausible number.

Everything in myMeal keeps calling ``units.to_grams``. This module is the one
place that knows foodcore exists, so the library can be swapped, upgraded or
removed without touching a caller. Only foodcore's **documented public API**
(``load_dataset``, ``Dataset.search_foods``/``portion_grams``/``food_record``/
``nutrient_per_100g``/``provenance_for``, ``foodcore.nutrition``'s record types
and ``compute_recipe``, ``Resolved``/``Unresolved``, ``rule_version``) is used
here; its internals are explicitly not a contract.

Two questions, one seam
-----------------------
``grams_per`` answers "what does one cup of this weigh?" (Stage 1).
``recipe_nutrition`` answers "what is this recipe's per-serving nutrition?"
(Stage 2). They share the identity machinery below — the scoring tiers, the
plural forms, the unrequested-state filter — because getting the *food* wrong
produces a confidently wrong number in both cases, and a second copy of that
policy is exactly the drift this module exists to prevent. They differ in the
final gate: a weight answer is checked against the other candidates' *portion
weights*, a nutrition answer against their *energy* (see ``_nutrition_food``).

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
import os
import re
import threading
from dataclasses import dataclass
from functools import lru_cache

from ..logsafe import scrub

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

#: foodcore **dataset** nutrient key -> the nutrient id its **registry**
#: (``foodcore.nutrition.NUTRIENTS``, which myMeal's stored field names match)
#: reports it under, with the unit of that registry entry.
#:
#: THE TWO VOCABULARIES ARE NOT THE SAME, and this is the only place that knows
#: it. The USDA adapter writes the keys it reads from FDC's nutrient names
#: ("energy", "carbohydrate", "fat_total"); the arithmetic registry declares the
#: ids a panel is labelled with ("calories", "carbs", "fat"). Three of the eight
#: differ. Asking the dataset for "calories" returns ``Unresolved`` —
#: indistinguishable from "this food has no data" — so a silent miss here would
#: not raise, would not log, and would make the whole feature quietly answer
#: "unknown" for every recipe. ``test_nutrient_key_map`` asserts both halves of
#: every row against the live dataset and the live registry so a rename on
#: either side fails a test instead of emptying a nutrition panel.
#:
#: Units are stated because they are a claim, not a convention: energy is kcal
#: (FDC also publishes kJ rows, which the adapter does not map), the macros are
#: grams and the minerals are milligrams. foodcore converts each value from the
#: unit the dataset row carries into the registry entry's unit, so a dataset
#: that changed units would convert rather than mislabel — but only if these
#: ids line up.
_DATASET_NUTRIENTS: dict[str, tuple[str, str]] = {
    "energy": ("calories", "kcal"),
    "protein": ("protein", "g"),
    "fat_total": ("fat", "g"),
    "fat_saturated": ("saturated_fat", "g"),
    "carbohydrate": ("carbs", "g"),
    "fiber": ("fiber", "g"),
    "sugars": ("sugar", "g"),
    "sodium": ("sodium", "mg"),
    "calcium": ("calcium", "mg"),
    "iron": ("iron", "mg"),
}

#: The dataset key whose values gate an ambiguous identity (see
#: ``_nutrition_food``). Energy, because every composition record in a USDA
#: import carries it and because it is the figure a cook actually reads.
_GATE_NUTRIENT = "energy"

#: Mass units, where grams are definitional physics rather than a lookup.
#: Mirrors ``units._WEIGHT_G``'s keys; named here rather than imported because
#: that name is private to ``units`` and this is a *claim about evidence*
#: ("these grams were computed, not estimated"), not a conversion table.
_MASS_UNITS = frozenset({"g", "kg", "oz", "lb"})

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
#: The path resolved with no Flask app context (the MCP server, a CLI, a test).
#: In a request ``settings_access`` hands back the app's already-resolved
#: object for the price of a dict lookup; outside one it calls
#: ``load_settings()``, which stats and parses /data/options.json and runs every
#: validator — once per ingredient line, which is not a price a render should
#: pay for a feature that is off. Both fields are restart-required, so caching
#: the answer for the process is exactly as correct as reading it at startup.
_ambient_path: str | None = None
_warned = False


def reset_cache() -> None:
    """Drop every cached handle and decision. For tests, and for a CLI that
    rebuilds the dataset in the same process."""
    global _ambient_path, _warned
    with _lock:
        _datasets.clear()
        _ambient_path = None
        _warned = False
        _lookup_grams.cache_clear()
        _lookup_composition.cache_clear()


def _setting(cfg, name: str, default):
    # getattr, not cfg.values[...]: a caller may hand us any object that looks
    # like settings, and an absent key must degrade to the default rather than
    # raise on a render path.
    try:
        return getattr(cfg, name)
    except Exception:  # noqa: BLE001 - config lookup must never break a render
        return default


def _path_from(cfg) -> str:
    if not _setting(cfg, "FOODCORE_ENABLED", True):
        return ""
    configured = str(_setting(cfg, "FOODCORE_DATASET", "") or "").strip()
    if configured:
        return configured
    # No explicit path: use the dataset foodcore ships. This is what makes the
    # feature work out of the box rather than only for an operator who built a
    # file by hand — the state it was in when it first shipped, where a correct
    # implementation delivered nothing to anybody.
    return shipped_dataset_path(cfg)


#: Where the shipped dataset is expanded to.
#:
#: EPHEMERAL on purpose. The obvious home is DATA_DIR — the only durable path an
#: add-on has — but Home Assistant backs that up wholesale, and this file is
#: 9.9 MB of data regenerable from a 2.3 MB asset inside the image. Measured,
#: expanding takes 0.05 s. Paying that once per container start is plainly
#: better than adding 10 MB to every snapshot the user ever takes of data they
#: already have.
#:
#: DATA_DIR remains the FALLBACK, for a deployment whose temp dir is unusable
#: (read-only, noexec, tiny tmpfs). There the durability is incidental; the
#: point is simply having somewhere to write.
_SHIPPED_NAME = "foodcore-usda-sr-legacy-2018-04.sqlite"


def shipped_dataset_path(cfg=None) -> str:
    """The expanded shipped dataset, expanding it on first use. "" if absent.

    foodcore ships the reference data gzipped (2.3 MB against 10.4 MB), because
    a frozen CC0 release is better committed than downloaded during a build that
    would then depend on a third party being reachable. SQLite cannot read a
    gzip stream, so it is expanded once into DATA_DIR and opened read-only
    thereafter.

    Every failure here degrades to "" — the pre-existing hand-table behaviour —
    and never raises. A read-only data dir, a half-written file from a killed
    container, a packaging change that drops the asset: none of them are worth
    taking the app down for a conversion refinement.
    """
    try:
        import foodcore
    except Exception:  # noqa: BLE001 - library absent is a valid deployment
        return ""

    source = os.path.join(os.path.dirname(os.path.abspath(foodcore.__file__)),
                          "data", "usda-sr-legacy-2018-04.sqlite.gz")
    if not os.path.isfile(source):
        return ""

    for directory in _expand_targets(cfg):
        target = os.path.join(directory, _SHIPPED_NAME)
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            return target
        if _expand_to(source, target):
            return target
    return ""


def _expand_targets(cfg) -> list[str]:
    """Temp first, data dir second — see the note on _SHIPPED_NAME."""
    import tempfile

    out = [tempfile.gettempdir()]
    data_dir = str(_setting(cfg, "DATA_DIR", "") or "").strip()
    if data_dir:
        out.append(data_dir)
    return [d for d in out if d]


def _expand_to(source: str, target: str) -> bool:
    """Expand the gzipped dataset, atomically. False on any failure.

    Never raises: a read-only directory, a full disk, or a packaging change
    that drops the asset must all cost the conversion refinement and not the
    app, which worked without any of this yesterday.
    """
    import gzip
    import shutil

    # Package DATA, not a sibling directory: foodcore is pip-installed, so the
    # asset has to travel inside the package. Deriving the path from the repo
    # layout worked in a checkout and silently found nothing once installed.
    # Expand to a temporary name and rename into place, so a container killed
    # mid-write leaves no truncated database for the next boot to open.
    tmp = f"{target}.partial-{os.getpid()}"
    try:
        with gzip.open(source, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        os.replace(tmp, target)
    except OSError as exc:
        log.warning(
            "could not expand the shipped food reference dataset to %s (%s); "
            "trying the next location", scrub(target), exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    log.info("expanded the shipped food reference dataset to %s", scrub(target))
    return True


def dataset_path(settings=None) -> str:
    """The configured dataset path, or "" when foodcore is not in use."""
    from flask import has_app_context

    from .ai.settings_access import resolved  # local: keeps imports light

    if settings is not None or has_app_context():
        return _path_from(resolved(settings))

    # No app context: memoise, per the note on _ambient_path.
    global _ambient_path
    if _ambient_path is None:
        _ambient_path = _path_from(resolved(None))
    return _ambient_path


def _dataset(settings=None):
    """The open dataset, or None when there is nothing configured/usable."""
    path = dataset_path(settings)
    if not path:
        return None
    # Opened under the lock. Two threads racing a first lookup would otherwise
    # both build a Dataset, and the loser's handle can already be pinned by the
    # lru_cache below — keeping its thread-local sqlite connections open for the
    # life of the process, and surviving reset_cache() by being written back
    # after the clear.
    with _lock:
        if path in _datasets:
            return _datasets[path]
        ds = None
        try:
            import foodcore

            # allow_minimal=False: we passed an explicit path, so the bundled
            # minimal dataset can never be what comes back — but saying so
            # means a future foodcore cannot quietly substitute it for a file
            # we named.
            ds = foodcore.load_dataset(path, allow_minimal=False)
        except Exception as exc:  # noqa: BLE001 - a bad dataset must not break the app
            # Named, not echoed: foodcore's DatasetError messages are curated,
            # but the path comes from operator config, so only the exception
            # class and the path we already know are logged.
            log.warning(
                "foodcore dataset at %r could not be opened (%s); falling back "
                "to myMeal's built-in densities",
                path, type(exc).__name__,
            )
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


#: States that change what a cup of something WEIGHS, as opposed to which food
#: it is. ``food_resolve.PREPARATION_WORDS`` deliberately strips these from an
#: ingredient line before matching, because "dried oregano" and "oregano" are
#: the same shopping item — correct there, and dangerous here, where a cup of
#: dried onion is a third of the weight of a cup of raw onion. So this list has
#: the opposite safety property to that one and therefore lives apart from it,
#: the same way PREPARATION_WORDS lives apart from the splitting qualifiers.
#:
#: Membership rule: a word belongs here only if it changes the MASS of a given
#: volume. "raw", "fresh", "chopped", "sliced", "large" do not and are absent —
#: a chopped/sliced spread is a disagreement between measured rows, which the
#: value gate already catches, not a different substance.
_STATE_WORDS = frozenset({
    "dried", "dehydrated", "freeze-dried", "powder", "powdered", "flakes",
    "granules", "granulated", "instant", "cooked", "boiled", "steamed",
    "braised", "roasted", "toasted", "fried", "canned", "frozen", "condensed",
    "concentrate", "concentrated", "puffed", "popped", "juice", "paste",
    "syrup", "extract", "freeze",
})


def _declares_unrequested_state(name: str, text_states: frozenset[str]) -> bool:
    """True when the dataset row names a state the cook did not ask for.

    "1 cup onions" must not be answered by "Onions, dehydrated flakes" (46 g
    against ~160 g raw). The check is symmetric and therefore also an
    improvement: "1 cup dried onions" now MAY use the dried row, because the
    word is in the line. It cannot be left to the value gate — two dried forms
    agree with each other perfectly and would endorse the wrong answer.
    """
    words = set(re.split(r"[^a-z0-9-]+", (name or "").lower()))
    return bool((words & _STATE_WORDS) - text_states)


def _candidates(ds, canonical: str, forms: frozenset[str]) -> list:
    """The pool to score, from the dataset's deliberately-dumb substring search.

    ``search_foods`` returns the SHORTEST matching names first, so on a real
    FDC import a plain search for "rice" comes back as eight short near-misses
    ("Rice cake", "Rice bran", "Rice milk") and the generic "Rice, white,
    long-grain, raw" never reaches the scorer — the dataset would be loaded and
    useless. Asking additionally for "<food>," targets USDA's description
    grammar, where the comma is exactly the boundary after the head noun, so
    the rows that can actually win the top tier are surfaced regardless of how
    many short branded names exist. Results are unioned and deduped; the whole
    lookup is memoised, so this costs a handful of queries per food per process.
    """
    rows, seen = [], set()
    for needle in (canonical, *(f"{form}," for form in sorted(forms))):
        for row in ds.search_foods(needle, _CANDIDATE_LIMIT):
            if row.key not in seen:
                seen.add(row.key)
                rows.append(row)
    return rows


def _resolve(ds, canonical: str, measures: tuple[str, ...],
             text_states: frozenset[str]) -> float | None:
    """Grams for one `measure` of `canonical`, or None if nothing qualifies.

    Confidence-tiered, because every kind of ambiguity here produces a
    confidently wrong number rather than a visible failure:

    * **state** — drop any row declaring a preparation the line did not ask
      for (``_declares_unrequested_state``).
    * **identity** — score what is left by the KIND of evidence and keep only
      the strongest tier. A tier below ``_MIN_SCORE`` never resolves.
    * **completeness** — every food in that tier must publish a weight for the
      measure. If only some do, the answer is being decided by which rows
      happen to be in the ``portion`` table, not by the evidence: "Milk, whole"
      having no cup row would otherwise hand "1 cup milk" to "Milk, dry,
      whole" (128 g against 244 g). An incomplete tier is ambiguity.
    * **value** — the weights that tier publishes must agree within
      ``_AGREEMENT`` to be interchangeable. If they don't, refuse.

    Ties are broken by shortest name, which in USDA's description grammar is
    the least-qualified (most generic) food — and it only ever picks between
    candidates that already agree about the weight.
    """
    import foodcore

    forms = _forms(canonical)
    candidates = [row for row in _candidates(ds, canonical, forms)
                  if not _declares_unrequested_state(row.name, text_states)]
    if not candidates:
        return None
    scored = [(_score(forms, row.name), len(row.name or ""), row) for row in candidates]
    best = max(s for s, _, _ in scored)
    if best < _MIN_SCORE:
        return None

    version = foodcore.rule_version("convert")
    values: list[float] = []
    for _s, _len, row in sorted(
        (item for item in scored if item[0] == best), key=lambda i: (i[1], i[2].name)
    ):
        grams = None
        for measure in measures:
            outcome = ds.portion_grams(row.key, measure, version=version)
            if outcome.resolved:
                grams = float(outcome.value)
                break
        # A zero is a broken row, not a fact about a food, and counts as no
        # weight — which makes the tier incomplete, which refuses.
        if not grams or grams <= 0:
            return None
        values.append(grams)

    if not values:
        return None
    if max(values) / min(values) > 1 + _AGREEMENT:
        return None
    return values[0]


#: Memoised per (dataset, food, measure, requested states). A recipe render
#: converts every line and a shopping list converts every recipe, so the same
#: handful of foods is asked for repeatedly; the dataset handle is cached per
#: path, so its identity is a stable cache key. The state set is part of the
#: key because it changes the answer ("dried onion" is not "onion"), and it is
#: empty for almost every line, so the hit rate survives.
@lru_cache(maxsize=2048)
def _lookup_grams(ds, canonical: str, measures: tuple[str, ...],
                  text_states: frozenset[str]) -> float | None:
    return _resolve(ds, canonical, measures, text_states)


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
        # The states the LINE asked for, read before canonicalisation strips
        # them: match_key removes "dried" (right for identity, wrong for mass),
        # so without this the dried/raw distinction would be unrecoverable.
        words = frozenset(re.split(r"[^a-z0-9-]+", (food_text or "").lower()))
        return _lookup_grams(ds, canonical.strip().lower(), measures,
                             frozenset(words & _STATE_WORDS))
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


# ===========================================================================
# Recipe nutrition (Stage 2)
#
# `(edible_grams / 100) x per_100g`, summed, divided by servings — but the
# arithmetic itself is `foodcore.nutrition.compute_recipe`'s and none of it is
# reimplemented here. This section's whole job is to put myMeal's two facts
# about an ingredient line (how many grams, and which food) into the shape that
# engine demands, and to refuse where the evidence does not justify an answer.
# ===========================================================================

#: A dataset state word -> the composition BASIS it implies. foodcore refuses a
#: raw record used as cooked rather than guessing a yield, so this mapping has
#: to be conservative: only the words that genuinely fix a basis map, and
#: "dry"/"dried"/"canned"/"frozen" deliberately do not — they describe a form,
#: not whether water has been driven off or taken up by cooking, and claiming a
#: basis we do not know would turn an honest `inferred` into a false `raw`.
_BASIS_FOR_STATE: dict[str, str] = {
    "raw": "raw",
    "cooked": "cooked",
    "boiled": "cooked",
}

#: Line words that say the cook is using the food COOKED. A superset of the
#: dataset's own state vocabulary, because a recipe writes "roasted"/"steamed"
#: where USDA writes "cooked".
_COOKED_WORDS = frozenset({
    "cooked", "boiled", "steamed", "braised", "roasted", "fried", "baked",
    "poached", "grilled", "sauteed", "sautéed",
})

#: How far the top identity tier's energy values may spread and still be
#: treated as the same food — ``_AGREEMENT``'s role, for nutrition. "Egg,
#: white, raw" and "Egg, whole, raw" both match the line "2 eggs" at the
#: head-segment tier and publish 52 and 143 kcal/100 g, a 175% spread. Picking
#: one silently is how a nutrition panel comes to be confidently wrong, so a
#: tier that disagrees refuses — and the recipe falls back to an AI estimate
#: which is at least labelled as a guess.
_ENERGY_AGREEMENT = 0.10


def _basis_of_record(record) -> str:
    """The basis a dataset record was measured on, from the states it declares."""
    import foodcore.nutrition as fcn

    for state in sorted(record.states):
        basis = _BASIS_FOR_STATE.get(state)
        if basis:
            return basis
    return fcn.BASIS_UNSPECIFIED


def _basis_used_as(words: frozenset[str]) -> str:
    """The basis the RECIPE LINE says it uses the food on.

    Only "cooked" is ever asserted. A line that says nothing gets
    ``unspecified``, NOT ``raw``: foodcore treats a one-sided ``unspecified``
    as "used as-is, method capped at inferred", which is an honest record of
    "we assumed the bases matched". Asserting ``raw`` would claim we knew.
    """
    import foodcore.nutrition as fcn

    return fcn.BASIS_COOKED if (words & _COOKED_WORDS) else fcn.BASIS_UNSPECIFIED


def _energy_of(record) -> float | None:
    value = record.nutrients.get(_GATE_NUTRIENT)
    return float(value[0]) if value else None


def _nutrition_food(ds, canonical: str, text_states: frozenset[str], used_as: str):
    """The one dataset food a line means, or None when nothing qualifies.

    The tiers are ``grams_per``'s, for the reason given in the module
    docstring. Two gates are this path's own:

    * **basis** — when the line says it uses the food cooked and some candidate
      was measured cooked, only those candidates remain. That is extra
      evidence, not a guess, and without it "cooked rice" ties raw rice
      (365 kcal) against cooked rice (130 kcal) and refuses a line it could
      have answered exactly.
    * **energy** — every candidate in the winning tier must publish energy and
      the values must agree within ``_ENERGY_AGREEMENT``. An incomplete tier is
      ambiguity (the answer would be decided by which rows happen to carry a
      nutrient row), and a disagreeing tier means choosing a number rather than
      reading one.
    """
    import foodcore.nutrition as fcn

    forms = _forms(canonical)
    candidates = [row for row in _candidates(ds, canonical, forms)
                  if not _declares_unrequested_state(row.name, text_states)]
    if not candidates:
        return None
    scored = [(_score(forms, row.name), len(row.name or ""), row) for row in candidates]
    best = max(s for s, _, _ in scored)
    if best < _MIN_SCORE:
        return None

    tier = sorted((item for item in scored if item[0] == best),
                  key=lambda i: (i[1], i[2].name))
    records = [r for r in (ds.food_record(row.key) for _s, _len, row in tier)
               if r is not None]
    if not records:
        return None

    if used_as != fcn.BASIS_UNSPECIFIED:
        exact = [r for r in records if _basis_of_record(r) == used_as]
        if exact:
            records = exact

    energies = [_energy_of(r) for r in records]
    if any(e is None or e < 0 for e in energies):
        return None
    hi, lo = max(energies), min(energies)
    # A tier of genuinely zero-energy foods (water, salt) agrees trivially, but
    # the ratio test cannot be applied to it — so say so rather than divide by
    # zero, and refuse a tier that mixes zero with non-zero.
    if lo <= 0:
        if hi > 0:
            return None
    elif hi / lo > 1 + _ENERGY_AGREEMENT:
        return None
    return records[0]


def _composition(ds, record, version: str):
    """A ``Composition`` for one dataset food, or None when it reports nothing.

    Every value comes back through ``nutrient_per_100g`` rather than being read
    off ``record.nutrients`` directly, because that is the call carrying the
    per-value provenance — method, confidence, and the snapshot it came from.
    ``Composition`` holds ONE provenance for the record, so the fields that can
    differ per nutrient are reduced the way foodcore reduces them elsewhere:
    weakest method, lowest confidence. A record is only as trustworthy as its
    least trustworthy value.
    """
    import foodcore.nutrition as fcn

    per_100g: dict = {}
    methods: list[str] = []
    confidences: list[float] = []
    for ds_key, (registry_id, _unit) in _DATASET_NUTRIENTS.items():
        if ds_key not in record.nutrients:
            continue
        outcome = ds.nutrient_per_100g(record.key, ds_key, version=version)
        if not outcome.resolved:
            continue
        per_100g[registry_id] = fcn.NutrientAmount(
            amount=float(outcome.value), unit=outcome.unit
        )
        methods.append(outcome.provenance.method)
        confidences.append(float(outcome.provenance.confidence))
    if not per_100g:
        return None
    return fcn.Composition(
        food_id=record.key,
        description=record.description,
        basis=_basis_of_record(record),
        per_100g=per_100g,
        provenance=ds.provenance_for(
            record.provider, record.source_id,
            fcn.weakest_method(*methods), min(confidences), version,
        ),
    )


@lru_cache(maxsize=2048)
def _lookup_composition(ds, canonical: str, text_states: frozenset[str],
                        used_as: str, version: str):
    """Memoised per (dataset, food, requested states, basis, rule version), for
    the reason ``_lookup_grams`` is: a recipe render asks for the same handful
    of foods repeatedly, and a composition costs one query per nutrient."""
    record = _nutrition_food(ds, canonical, text_states, used_as)
    return _composition(ds, record, version) if record is not None else None


def _grams_outcome(line: str, parsed: dict, version: str):
    """``Resolved`` grams for one line, or ``Unresolved`` with a reason.

    Grams come from ``units.to_grams`` — myMeal's existing, already-tested
    answer, which itself consults this module for volume measures and falls
    back to the density table. A second gram calculation here would let the
    weight shown beside an ingredient and the weight its calories were
    computed from disagree.
    """
    import foodcore
    import foodcore.nutrition as fcn

    from . import units  # local: keeps this module import-light

    grams = units.to_grams(line)
    if grams is None or grams <= 0:
        return fcn.Unresolved(
            reason="no_gram_weight",
            detail=(
                f"{line}: myMeal cannot weigh this line, so it has no mass to "
                "contribute and no denominator to count against."
            ),
        )
    # Definitional for a mass unit, derived for anything else. foodcore's
    # weakest-link rule reads this, so over-claiming here would let "3 medium
    # onions" produce a figure labelled `calculated`.
    definitional = parsed.get("unit") in _MASS_UNITS
    return fcn.Resolved(
        value=float(grams),
        unit="g",
        provenance=foodcore.Provenance(
            source="internal",
            source_id="",
            retrieved="",
            method="calculated" if definitional else "estimated",
            version=version,
            confidence=1.0 if definitional else 0.9,
            # "internal" is the honest licence for a number myMeal's own unit
            # code produced. It AND-joins with the dataset's CC0 on the way
            # out, so a derived total never claims a licence nothing granted.
            license="internal",
        ),
    )


def recipe_nutrition(lines, servings, nutrients=None, settings=None):
    """Deterministic per-serving nutrition for a recipe, or ``None``.

    ``None`` means **this module declined to answer**, and the caller must fall
    back to whatever it did before: no dataset configured (the shipped
    default), the dataset unusable, no ingredient lines, or any unexpected
    failure. It never means "zero", and it is never an exception, because this
    sits on the same request path as a recipe render.

    A ``RecipeNutrition`` means the arithmetic ran. That is still not a promise
    that every nutrient has a value — each one carries its own completeness and
    is ``Unresolved`` below foodcore's floor. Reading ``.value`` (never
    ``.partial_value``) is what keeps "unknown" from being printed as a number.

    ``nutrients`` is the registry ids to report, supplied by the caller so this
    module needs no opinion about which fields myMeal stores. ``None`` reports
    the union of what the matched records happen to publish, which is useful
    for diagnostics and wrong for a panel: a nutrient no record mentions would
    simply not appear, rather than appearing as an explicit absence.
    """
    global _warned
    try:
        texts = [str(line).strip() for line in (lines or []) if str(line).strip()]
        if not texts:
            return None
        ds = _dataset(settings)
        if ds is None:
            return None

        import foodcore
        import foodcore.nutrition as fcn

        from . import food_resolve, units  # local: keeps this module import-light

        version = foodcore.rule_version("nutrition")
        recipe_lines = []
        for text in texts:
            parsed = units.parse_line(text)
            words = frozenset(re.split(r"[^a-z0-9-]+", text.lower()))
            used_as = _basis_used_as(words)
            canonical = food_resolve.match_key(parsed.get("rest") or text)
            composition = None
            if canonical:
                composition = _lookup_composition(
                    ds, canonical.strip().lower(),
                    frozenset(words & _STATE_WORDS), used_as, version,
                )
            recipe_lines.append(fcn.RecipeLine(
                label=text,
                edible_grams=_grams_outcome(text, parsed, version),
                composition=composition if composition is not None else fcn.Unresolved(
                    reason="no_composition",
                    detail=(
                        f"{text}: the reference dataset has no food this line "
                        "unambiguously names. This is not zero."
                    ),
                ),
                used_as=used_as,
            ))

        return fcn.compute_recipe(
            fcn.RecipeInput(label="", servings=servings or None, lines=recipe_lines),
            nutrients=tuple(nutrients) if nutrients else None,
        )
    except Exception as exc:  # noqa: BLE001 - nutrition must never break a request
        if not _warned:
            _warned = True
            log.warning(
                "foodcore nutrition failed (%s); myMeal will fall back to an AI "
                "estimate for the rest of this process", type(exc).__name__,
            )
        else:
            log.debug("foodcore nutrition failed (%s)", type(exc).__name__)
        return None


# ===========================================================================
# Recipe safety (Stage 3): allergen screening + dietary compatibility
#
# `foodcore.allergens` / `foodcore.dietary` are PURE RULE ENGINES — no dataset,
# no sqlite, no I/O. So unlike `grams_per` and `recipe_nutrition` above, nothing
# here is gated on `MYMEAL_FOODCORE_DATASET`: the rules ship inside the package,
# so screening works on a stock install with no operator setup at all.
#
# ---------------------------------------------------------------------------
# TWO LEXICONS, DELIBERATELY NOT RECONCILED. Do not "fix" this.
# ---------------------------------------------------------------------------
# `food_resolve.py` seeds `Food.allergens`; this seam screens the ingredient
# TEXT through foodcore. They disagree, on purpose, and the canonical instance
# is coconut:
#
#   food_resolve.py  "coconut": ("fruit", ())   -> no allergens
#   foodcore         "coconut milk"             -> tree_nuts: CONTAINS
#
# Both are right in their own frame. `food_resolve`'s question is "which
# material is this, and may a substring match cross into another material?"
# (ADR-0001: almond milk is not milk) — botany answers that, and a coconut is a
# drupe. foodcore's question is "what must a US label declare?" — and the FDA
# counts coconut as a tree nut.
#
# Merging them yields ONE list that is wrong for one of its two consumers: fold
# the FDA view into `Food.allergens` and the material-boundary rule starts
# treating coconut as a nut for substitution and canonicalisation; fold botany
# into foodcore and someone avoiding tree nuts is served a coconut curry.
#
# So: this module NEVER READS `Food.allergens`, and nothing here writes it.
# foodcore's verdicts are the labelling answer and the only thing this seam
# reports; `Food.allergens` stays exactly what it is, for exactly its own
# callers. See docs/food-intelligence-platform.md §3.2.
#
# ---------------------------------------------------------------------------
# AND: an empty `Food.allergens` is not "free from".
# ---------------------------------------------------------------------------
# `Food.allergens` is `[]` for everything a user typed, which means "nobody has
# said", not "contains nothing". That is the whole reason this seam exists, and
# it is why `[]` has no path to the wire from here: the four states come from
# foodcore, `NO_KNOWN_INGREDIENT_IDENTIFIED` is the closest one to "nothing
# found", and even that one says "(not a safety claim)" in its own `str()`.
#
# ---------------------------------------------------------------------------
# No boolean without a policy — kept, not routed around.
# ---------------------------------------------------------------------------
# foodcore makes `bool(verdict)` raise and gives `permitted_under(policy)` /
# `decide(policy)` no default. `RecipeSafety` below mirrors that (its own
# `__bool__` raises) and this module adds NO convenience predicate: there is no
# `is_safe`, no `free_of`, no `allergen_free`. A caller that wants a boolean
# reaches through to `safety.allergens.decide(policy)` and therefore has to name
# the policy, in code, where review can see it. `screening_policies()` hands the
# enums over so doing that never requires a second foodcore importer.
#
# Which allergens and which diets are screened is foodcore's own default set
# (`US_MAJOR_ALLERGENS`, `DIET_KEYS`), read from the library rather than
# restated here. A local copy of either list would be a second place for the
# policy to live, and the ruleset is versioned precisely so the answer may
# change — every verdict carries the version it was produced under.
# ===========================================================================


class ScreeningUnavailable(RuntimeError):
    """Screening could not run.

    A TYPED domain exception carrying a curated message with no upstream text
    in it, because "we cannot tell you" must never be rendered the same way as
    "nothing to report". The API turns it into a 503, not an empty panel.
    """


class NothingToScreen(ScreeningUnavailable):
    """There were no ingredient lines.

    Separate from its parent because it is not a malfunction: a recipe with no
    ingredients is a perfectly normal draft. It is still not a success — a panel
    reading "no allergens found" over zero screened lines is exactly the false
    clearance this seam exists to prevent — so the caller must handle it
    explicitly rather than receive an empty verdict list.
    """


@dataclass(frozen=True)
class SafetyLine:
    """One ingredient line to screen, as plain data.

    `text` is the line AS WRITTEN, not the resolved food name. foodcore's
    `normalize_text` keeps commas and parentheticals precisely because that is
    where allergen information hides ("margarine (contains milk)", "bread, may
    contain sesame"); handing it `food.name` would discard that, and would also
    launder the author's words through a canonicalisation that deliberately
    strips preparation. `optional` comes from `units.annotations` — myMeal's
    existing single answer to "is this line optional" — so there is no second
    parser of the same text.
    """

    text: str
    optional: bool = False


@dataclass(frozen=True)
class RecipeSafety:
    """Both screenings for one recipe, plus the lines they ran over.

    Has no truth value, for the same reason `AllergenScreening` has none: the
    shortest wrong thing to write (`if safety:`) must fail loudly instead of
    meaning something plausible and incorrect.
    """

    allergens: object  # foodcore AllergenScreening
    diets: object      # foodcore DietaryScreening
    lines: tuple[SafetyLine, ...]

    def __bool__(self) -> bool:
        raise TypeError(
            "RecipeSafety has no truth value, on purpose: 'nothing found' is "
            "not 'safe'. Call safety.allergens.decide(policy) or "
            "safety.diets.decide(policy) with an explicit policy (STRICT "
            "excludes unknown; PREFERENCE does not)."
        )


def _safety_engines():
    """The two foodcore engines, or `ScreeningUnavailable`.

    Imported lazily and at the point of USE: `import foodcore` failing is a
    valid deployment of the rest of myMeal and must cost this panel rather than
    the app. Only the error's class name is logged — its message could carry a
    filesystem path.
    """
    try:
        import foodcore.allergens as fca
        import foodcore.dietary as fcd
    except Exception as exc:  # noqa: BLE001 - library absent is a valid deployment
        log.warning("foodcore allergen/dietary engines unavailable (%s)",
                    type(exc).__name__)
        raise ScreeningUnavailable(
            "Allergen and dietary screening is unavailable on this install."
        ) from None
    return fca, fcd


def screening_policies():
    """`(AvoidancePolicy, DietPolicy)`, so a future filter can name a policy
    without becoming a second foodcore importer. Raises `ScreeningUnavailable`
    when the engines are missing."""
    fca, fcd = _safety_engines()
    return fca.AvoidancePolicy, fcd.DietPolicy


def screen_lines(lines) -> RecipeSafety:
    """Screen ingredient lines for allergens and dietary compatibility.

    `lines` is an iterable of `SafetyLine` or plain strings; blank ones are
    dropped. Returns a `RecipeSafety`, or raises — it never returns a
    falsely-empty result. `NothingToScreen` when there was nothing to read,
    `ScreeningUnavailable` when the engines are absent or failed.

    Unlike everything above in this module, a failure here is NOT swallowed into
    a `None` that the caller renders as "no data". Grams and calories degrade to
    an older answer; "what does this contain" has no older answer to degrade to,
    so silence would be indistinguishable from a clean result.
    """
    fca, fcd = _safety_engines()
    rows = tuple(
        line if isinstance(line, SafetyLine) else SafetyLine(text=str(line))
        for line in (lines or [])
    )
    rows = tuple(r for r in rows if r.text.strip())
    if not rows:
        raise NothingToScreen(
            "This recipe has no ingredient lines, so there is nothing to screen."
        )

    try:
        # Inside the guard, not above it: building the refs is already a call
        # into the library, and a test that exploded on attribute access found
        # this escaping as a raw RuntimeError.
        refs = [fca.IngredientRef(text=r.text, optional=r.optional) for r in rows]
        # foodcore's own defaults for both sets — see the note above on why this
        # module keeps no copy of either list.
        allergens = fca.screen(refs, fca.SensitivityProfile.us_major_allergens())
        diets = fcd.screen_diets(refs)
    except Exception as exc:  # noqa: BLE001 - never echo the engine's own text
        log.warning("foodcore screening failed (%s)", type(exc).__name__)
        raise ScreeningUnavailable(
            "Allergen and dietary screening could not be completed for this "
            "recipe."
        ) from None
    return RecipeSafety(allergens=allergens, diets=diets, lines=rows)
