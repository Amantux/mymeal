"""Learned unit→weight conversions: look one up, remember it, show where it came from.

``services.units`` ships densities for volume units of common foods. It cannot
ship a weight for a *count* unit — there is no honest gram value for "a clove" —
but plenty of specific pairs do have a real, well-known answer: a stick of butter
is 113 g, a standard can of tomatoes is 400 g. When a recipe needs one of those
and the app does not know it, this module asks the web once and remembers the
answer.

The rules, in priority order:

1. **Built-in physics always wins.** ``units.to_grams`` consults this module only
   after its own weight and density tables, so a looked-up number can never
   override a shipped one.
2. **A pending conversion is never used in a calculation.** Below the confidence
   threshold it is stored for review and treated as if it did not exist.
3. **Provenance is recorded, always.** ``source`` and ``source_url`` are what let
   the UI say "≈113 g (from the web)" rather than presenting a number the app
   found on the internet as though it shipped with one.
4. **The lookup never runs on a read path.** ``resolver()`` — used by rendering —
   only reads the cache. ``learn()`` is called explicitly, during import.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from ..extensions import db
from ..models.unit_conversion import UnitConversion
from . import food_resolve, units, websearch

_LOGGER = logging.getLogger("mymeal.conversions")

# A lookup is worth doing for a handful of unfamiliar units in one import, not
# for every line of a badly-scraped 60-ingredient page.
MAX_LOOKUPS_PER_IMPORT = 5

# Sanity bounds on a per-unit weight. Below this is a rounding artefact ("0 g"),
# above it the model or page is talking about a case or a sack, not an
# ingredient. Either way, storing it would produce nonsense weights forever.
MIN_GRAMS = 0.1
MAX_GRAMS = 5000.0

# The model columns. Clamped at the write site because an over-long value is a
# DataError on PostgreSQL (SQLite silently accepts it, which is exactly why this
# was invisible in the tests) and a failed flush poisons the whole session.
MAX_UNIT_CHARS = 40
MAX_TERM_CHARS = 120

# "113 g", "113g", "113 grams", "approximately 400 g". Deliberately grams only:
# accepting ounces here would mean trusting our own unit inference about a
# sentence we did not write, on top of trusting the sentence.
_GRAMS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:g\b|grams?\b)", re.IGNORECASE)


def _safe_source_url(raw) -> str:
    """Keep a search result's URL only if it is http(s).

    This value comes from an external search API and is rendered as an href, so
    a ``javascript:`` or ``data:`` address would become a click target. Validated
    where it is STORED as well as where it is used — an allowlist, so anything
    invented later is rejected by not being on it.
    """
    value = str(raw or "").strip()[:1024]
    try:
        scheme = urlparse(value).scheme.lower()
    except ValueError:
        return ""
    return value if scheme in ("http", "https") else ""


# Moved to food_resolve.PREPARATION_WORDS so units._density_for keys the same
# way; re-exported because this name is the documented one.
_NOISE = food_resolve.PREPARATION_WORDS


def food_term(text: str) -> str:
    """Reduce an ingredient's food text to a stable cache key.

    "unsalted butter, softened" and "butter (softened)" must hit the same row, or
    the cache never hits and every recipe pays for another lookup.
    """
    # Everything after a comma is preparation, not identity.
    head = str(text or "").split(",")[0]
    return food_resolve.match_key(head)[:MAX_TERM_CHARS]


def _threshold() -> float:
    from flask import current_app
    return float(current_app.config.get("AI_CONFIDENCE_THRESHOLD", 0.8))


def resolver(group_id: str | None):
    """A ``(unit, food) -> grams_per_unit | None`` callable over the cache.

    Read-only and confirmed-only: passed into ``units.to_grams`` on render paths,
    where it must never trigger a network call and must never surface a value a
    human has not accepted.
    """
    cache: dict[tuple[str, str], float | None] = {}

    def lookup(unit: str | None, food: str) -> float | None:
        if not unit:
            return None
        key = (unit, food_term(food))
        if not key[1]:
            return None
        if key in cache:
            return cache[key]
        row = db.session.query(UnitConversion).filter_by(
            group_id=group_id, unit=key[0], food_term=key[1],
            status="confirmed",
        ).first()
        cache[key] = row.grams_per_unit if row else None
        return cache[key]

    return lookup


# Phrases that mean the number belongs to a nutrition panel or a pack label, not
# to the question we asked. "Serving size 100 g. A stick of butter is 113 g"
# otherwise answers 100 — the first number in the snippet is frequently not its
# answer, which is how a plausible-looking wrong weight gets learned.
_NOT_THE_ANSWER = (
    "serving size", "per serving", "servings per", "nutrition", "calories",
    "net weight", "daily value", "per 100",
)


def parse_grams(text: str) -> float | None:
    """The gram figure a search snippet is asserting, or None.

    Figures introduced by nutrition-panel language are skipped; of what remains
    the first is taken, since a snippet leads with its answer.
    """
    haystack = text or ""
    for match in _GRAMS_RE.finditer(haystack):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if not MIN_GRAMS <= value <= MAX_GRAMS:
            continue
        # Look back a short way for what introduced this number.
        lead = haystack[max(0, match.start() - 40):match.start()].lower()
        if any(phrase in lead for phrase in _NOT_THE_ANSWER):
            continue
        return value
    return None


def learn(unit: str, food: str, group_id: str | None, *, settings=None) -> UnitConversion | None:
    """Look a conversion up on the web and cache it. Returns None on any failure.

    Fail-open like everything else in this path: no search key, no results, no
    number in the results, an implausible number — all of them mean the app
    carries on without a weight for that line, which is exactly today's
    behaviour.
    """
    term = food_term(food)
    canonical = units.canonical_unit(unit)
    if not canonical or not term:
        return None

    existing = db.session.query(UnitConversion).filter_by(
        group_id=group_id, unit=canonical, food_term=term,
    ).first()
    # Already known — including a pending row. Asking again would spend a search
    # to re-derive an answer a human has already been asked to review.
    if existing:
        return existing

    if not websearch.enabled(settings):
        return None

    query = f"how many grams is 1 {canonical} of {term}"
    results = websearch.web_search(query, max_results=3, settings=settings)
    if not results:
        return None

    # Agreement across independent pages is the only confidence signal available
    # here, and it is a real one: two sites saying 113 g is meaningfully better
    # evidence than one saying it.
    answers: list[tuple[float, str]] = []
    for result in results[:3]:
        grams = parse_grams(f"{result.get('title', '')} {result.get('content', '')}")
        if grams is not None:
            answers.append((grams, _safe_source_url(result.get("url"))))
    if not answers:
        return None

    best, url = answers[0]
    # Within 10% counts as agreement — sources round differently (113 vs 115 g
    # for a stick of butter) and treating that as disagreement would reject the
    # answers that are actually right. Counted by DISTINCT HOST: two syndications
    # of one article, or two pages from one site, are one source agreeing with
    # itself, and letting that auto-confirm was the weakest link in this gate.
    hosts = {urlparse(u).netloc.lower() for grams, u in answers
             if abs(grams - best) <= best * 0.1 and u}
    # Deliberately below the 0.8 default threshold at two hosts: corroboration
    # earns review, not automatic trust. Three independent hosts saying the same
    # number is the bar for using it without a human.
    confidence = {0: 0.4, 1: 0.5, 2: 0.7}.get(len(hosts), 0.95)

    row = UnitConversion(
        unit=canonical[:MAX_UNIT_CHARS], food_term=term, grams_per_unit=best,
        source="web", source_url=url, confidence=confidence,
        status="confirmed" if confidence >= _threshold() else "pending",
        group_id=group_id,
    )
    db.session.add(row)
    # flush(), not commit() — the request owns the single commit, so a failure
    # later in the import rolls this back with everything else.
    db.session.flush()
    _LOGGER.info("learned conversion: 1 %s %s = %sg (%s, %s)",
                 canonical, term, best, row.status, confidence)
    return row


def learn_for_lines(displays: list[str], group_id: str | None, *, settings=None) -> int:
    """Look up conversions for the unit+food pairs these lines actually use.

    Bounded by :data:`MAX_LOOKUPS_PER_IMPORT` and only for pairs the built-in
    tables cannot already answer — so a recipe written in grams and cups costs
    nothing. Returns how many rows were created.
    """
    wanted: list[tuple[str, str]] = []
    seen = set()
    for display in displays or []:
        parsed = units.parse_line(display or "")
        if not parsed["unit"] or parsed["qty"] is None:
            continue
        # A pair the shipped tables already answer needs no lookup.
        if units.to_grams(display) is not None:
            continue
        term = food_term(parsed["rest"])
        key = (parsed["unit"], term)
        if not term or key in seen:
            continue
        seen.add(key)
        wanted.append(key)

    learned = 0
    for unit, term in wanted[:MAX_LOOKUPS_PER_IMPORT]:
        try:
            # Skip pairs already known, rather than calling learn() and
            # discarding the cache hit: that counted a hit as a creation, so a
            # re-import of a recipe the app already understood fired a pointless
            # commit and reported work it had not done.
            known = db.session.query(UnitConversion.id).filter_by(
                group_id=group_id, unit=unit, food_term=term).first()
            if known:
                continue
            if learn(unit, term, group_id, settings=settings) is not None:
                learned += 1
        except Exception as exc:  # noqa: BLE001 - never fail an import for this
            # A failed flush leaves the connection in an aborted transaction on
            # PostgreSQL; without this the caller's next statement dies with
            # InFailedSqlTransaction and the user is told the import failed
            # after their recipe was already saved.
            db.session.rollback()
            _LOGGER.warning("conversion lookup failed for %s/%s: %s",
                            unit, term, exc)
    return learned
