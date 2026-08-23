"""A second pass over the recipe-level fields a deterministic read left empty.

The sibling of ``ingredient_ai``, which does this for individual ingredient
lines, and it follows the same three rules for the same reasons:

1. **Only what the parser could not read is sent.** A recipe that parsed cleanly
   never reaches the model, so the fast path stays instant and free.
2. **Fail open, always.** No provider, a timeout, malformed JSON, an answer that
   fails a check — every one of those keeps the deterministic result untouched.
   The model is an improvement on the product, not the product.
3. **Nothing already read is overwritten, and nothing is invented.** The model
   may only fill a hole, and what it returns is checked against the source text
   before it is accepted.

Rule 3 is doing real work here. Asking a language model for a recipe's METHOD
when the text has none is an open invitation to write one, and a plausible
invented method is far worse than an empty one — the user would have no way to
know it wasn't in what they pasted. So every proposal must be *grounded*: it has
to appear in the source, as written, numbers included.

What grounding does NOT protect against, and is not meant to: text the user
pasted telling the model what to say. An injected instruction is *in the source*,
so anything echoing it is grounded by construction. The limits there are the
field bounds, not the grounding check — a name is clamped to 200 chars, steps to
40 x 2000, and servings to 1..100, so the worst case is prose the user already
pasted appearing in their own recipe. Vue escapes it on render.
"""
from __future__ import annotations

import logging
import re

from ..logsafe import scrub
from .ai.base import safe_upstream_detail
from .recipe_parse import strip_step_number

_LOGGER = logging.getLogger("mymeal.recipe_complete")

# Times are deliberately not asked for: a wrong prep time is a small annoyance,
# whereas a missing method or serving count makes the recipe unusable (scaling is
# driven by servings).
PLACEHOLDER_NAME = "Imported Recipe"
MAX_SOURCE_CHARS = 8000
MAX_STEPS = 40
# Share of a proposal's word n-grams that must appear in the source for it to
# count as read-from-the-text rather than written-by-the-model. Not 1.0: a model
# legitimately drops a "1." prefix or joins a hard-wrapped line, and those shift
# a few shingles at the edges.
_GROUNDING = 0.7
# n-gram width. 4 is long enough that a fabricated sentence cannot hit it by
# reusing common cooking words, and short enough to survive minor rewording.
_SHINGLE = 4

_SYSTEM = (
    "You extract recipe fields from text the user pasted. Return ONLY what the "
    "text actually says. Never write a method that is not there, never invent a "
    "serving count, and never translate or re-title the recipe. If the text does "
    "not state a field, return null for it. It is correct and expected to return "
    "null — an empty answer is better than a plausible guess."
)


def _prompt(source: str, missing: list[str]) -> str:
    wanted = {
        "name": '"name": the recipe\'s title as written, or null',
        "steps": '"steps": an array of the method steps, in order, each the '
                 'instruction text as written (drop any "1." numbering), or null',
        "servings": '"servings": the number of servings as an integer, or null',
    }
    keys = "\n".join(f"  {wanted[f]}" for f in missing if f in wanted)
    return (
        f"Return JSON with exactly these keys:\n{keys}\n\n"
        "Only these keys. Do not return ingredients — those are already known.\n\n"
        f"Text:\n\n{source[:MAX_SOURCE_CHARS]}"
    )


def missing_fields(payload: dict) -> list[str]:
    """Which of the fields we would ask a model for are actually empty."""
    out = []
    name = str(payload.get("name") or "").strip()
    if not name or name == PLACEHOLDER_NAME:
        out.append("name")
    if not (payload.get("steps") or []):
        out.append("steps")
    try:
        servings = int(payload.get("servings") or 0)
    except (TypeError, ValueError):
        servings = 0
    if servings <= 0:
        out.append("servings")
    return out


def _tokens(text: str) -> list[str]:
    """Lower-cased words AND numbers, in order. Whitespace is normalised so a
    hard-wrapped source line still matches the joined sentence a model returns."""
    return re.findall(r"[^\W_]+", (text or "").lower())


def _shingles(tokens: list[str], n: int = _SHINGLE) -> set[tuple]:
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def _numbers(text: str) -> set[str]:
    """Numeric tokens, kept as written ("180", "45", "1.5")."""
    return set(re.findall(r"\d+(?:[.,]\d+)?", text or ""))


def _grounded(candidate: str, source_tokens: list[str], source_numbers: set[str],
              source_shingles: set[tuple]) -> bool:
    """Whether a proposal was actually READ FROM the source, not merely built
    from its vocabulary.

    Two gates, because the obvious one is not enough:

    1. **Every number must appear in the source.** A bag-of-words check cannot
       see digits at all, so "Bake for 45 minutes" and "Bake for 5 minutes" were
       indistinguishable — a model could silently halve a cook time or move an
       oven from 180C to 240C and pass. In a cooking app that is the most
       dangerous thing this module could do, so it is a hard gate, not a ratio.
    2. **Word ORDER must match**, checked as overlapping n-grams rather than a
       set. Recipe prose has a small, repetitive vocabulary ("heat", "add",
       "until", "minutes", "pan"), so a set test passes almost any fluent
       cooking sentence built from it — including inversions like "Do not cook
       the chicken" and fabrications like "Serve the chicken raw in the stock".
       Shingles require the words to appear together, in sequence, as written.
    """
    if not _numbers(candidate) <= source_numbers:
        return False
    tokens = _tokens(candidate)
    if not tokens:
        return False
    if len(tokens) < _SHINGLE:
        # Too short to shingle (a two-word title): require it verbatim, in order.
        return any(tokens == source_tokens[i:i + len(tokens)]
                   for i in range(len(source_tokens) - len(tokens) + 1))
    mine = _shingles(tokens)
    return len(mine & source_shingles) / len(mine) >= _GROUNDING


def complete(payload: dict, source_text: str, provider=None) -> dict:
    """Return ``payload`` with any missing fields filled in from ``source_text``.

    Never raises, never overwrites a field that was already read, and returns the
    payload unchanged whenever the model is unavailable or unconvincing.
    """
    missing = missing_fields(payload)
    source = (source_text or "").strip()
    if not missing or not source:
        return payload

    if provider is None:
        try:
            from .ai.registry import get_provider
            provider = get_provider()
        except Exception as exc:  # noqa: BLE001 - no provider is a normal state
            _LOGGER.info("recipe completion skipped: %s", scrub(str(exc)))
            return payload

    try:
        raw = provider.complete_json(_prompt(source, missing), system=_SYSTEM)
    except Exception as exc:  # noqa: BLE001 - never fail an import for this
        # scrub: an upstream provider body can carry CR/LF and forge a log
        # entry (CWE-117). api/ai.py does the same at its provider boundaries.
        _LOGGER.warning("recipe completion failed: %s",
                        scrub(safe_upstream_detail(exc)))
        return payload
    if not isinstance(raw, dict):
        return payload

    source_tokens = _tokens(source)
    source_numbers = _numbers(source)
    source_shingles = _shingles(source_tokens)
    def grounded(candidate: str) -> bool:
        return _grounded(candidate, source_tokens, source_numbers, source_shingles)

    out = dict(payload)
    filled = []

    if "name" in missing:
        name = str(raw.get("name") or "").strip()[:200]
        if name and grounded(name):
            out["name"] = name
            filled.append("name")

    if "steps" in missing:
        steps = raw.get("steps")
        if isinstance(steps, list):
            kept = []
            for item in steps[:MAX_STEPS]:
                # `or ""` INSIDE the get: a model answering with a different key
                # ({"instruction": ...}) made str(None) the literal step "None",
                # which then passed grounding on any source containing that word.
                raw_text = item.get("text") or "" if isinstance(item, dict) else item
                # Strip "1." / "Step 2:" before grounding as well as before
                # storing: the prefix adds a number that isn't in the source, so
                # the numeric gate would reject an otherwise genuine step.
                text = strip_step_number(str(raw_text or "").strip())
                # Every step is checked individually: a model that read three real
                # steps and then padded with a fourth loses only the fourth.
                if text and grounded(text):
                    kept.append({"title": "", "text": text[:2000]})
            if kept:
                out["steps"] = kept
                filled.append("steps")
                # The oven temperature is derived from the steps at PARSE time,
                # so a recipe whose method arrives here stored no temperature at
                # all — the one field with its own column and converter.
                if not out.get("cookTemperatureC"):
                    from .cooking import parse_temperature
                    out["cookTemperatureC"] = parse_temperature(
                        " ".join(s["text"] for s in kept))

    if "servings" in missing:
        try:
            servings = int(raw.get("servings") or 0)
        except (TypeError, ValueError):
            servings = 0
        # A serving count is a bare number, so grounding cannot check it. Bound it
        # to something a household recipe could plausibly say instead.
        if 1 <= servings <= 100:
            out["servings"] = servings
            filled.append("servings")

    if filled:
        _LOGGER.info("recipe completion filled: %s", ", ".join(filled))
    return out
