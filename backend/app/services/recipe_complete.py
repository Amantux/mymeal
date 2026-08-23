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
know it wasn't in what they pasted. So every proposal is required to be
*grounded*: its words have to actually appear in the source.
"""
from __future__ import annotations

import logging
import re

_LOGGER = logging.getLogger("mymeal.recipe_complete")

# The fields worth a model call when missing. Times are deliberately absent:
# a wrong prep time is a small annoyance, whereas a missing method or serving
# count makes the recipe unusable (scaling is driven by servings).
_FIELDS = ("name", "steps", "servings")

PLACEHOLDER_NAME = "Imported Recipe"
MAX_SOURCE_CHARS = 8000
MAX_STEPS = 40
# Share of a proposal's words that must appear in the source for it to count as
# read-from-the-text rather than written-by-the-model.
_GROUNDING = 0.7

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


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]+", (text or "").lower()) if len(w) > 2}


def _grounded(candidate: str, source_words: set[str]) -> bool:
    """Whether a proposal's words actually occur in the source text.

    The anti-invention check. A model asked for a method it cannot find will
    happily produce a fluent one; those sentences are made of words that are not
    in the source, so they fail here and are dropped.
    """
    words = _words(candidate)
    if not words:
        return False
    hits = len(words & source_words)
    return hits / len(words) >= _GROUNDING


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
            _LOGGER.info("recipe completion skipped: %s", exc)
            return payload

    try:
        raw = provider.complete_json(_prompt(source, missing), system=_SYSTEM)
    except Exception as exc:  # noqa: BLE001 - never fail an import for this
        _LOGGER.warning("recipe completion failed: %s", exc)
        return payload
    if not isinstance(raw, dict):
        return payload

    source_words = _words(source)
    out = dict(payload)
    filled = []

    if "name" in missing:
        name = str(raw.get("name") or "").strip()[:200]
        if name and _grounded(name, source_words):
            out["name"] = name
            filled.append("name")

    if "steps" in missing:
        steps = raw.get("steps")
        if isinstance(steps, list):
            kept = []
            for item in steps[:MAX_STEPS]:
                text = str(item.get("text") if isinstance(item, dict) else item or "")
                text = text.strip()
                # Every step is checked individually: a model that read three real
                # steps and then padded with a fourth loses only the fourth.
                if text and _grounded(text, source_words):
                    kept.append({"title": "", "text": text[:2000]})
            if kept:
                out["steps"] = kept
                filled.append("steps")

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
