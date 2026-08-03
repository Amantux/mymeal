"""A second pass over ingredient lines the deterministic parser could not read.

`units.parse_line` handles the common scraped shapes. What defeats it is prose:
"a good handful of parsley", "juice of 1 lemon", "2 sticks butter, cubed". Those
lines end up with no quantity and no unit, so they scale wrong and consolidate
badly on a shopping list.

Three rules shape everything here:

1. **Only the lines the parser could not structure are sent.** A line it read
   confidently is never shown to the model — the model cannot overwrite a
   deterministic answer, the prompt stays small enough for a genuinely small
   local model, and a cleanly-marked-up recipe skips the model entirely.
2. **Fail open, always.** No provider, a timeout, malformed JSON, a unit we do
   not know, a quantity that is not a number — every one of those keeps the
   deterministic result. An import must never fail because a model was
   unavailable; the model is an improvement on the product, not the product.
3. **The human's line is never rewritten.** ``display`` is the source of truth.
   Everything the model returns is a *proposal* attached alongside it, for the
   confirmation step to accept or discard.
"""
from __future__ import annotations

import logging

from . import units

_LOGGER = logging.getLogger("mymeal.ingredient_ai")

# A local SLM gets slow and vague on long lists, and a recipe with more unparsed
# lines than this is usually a bad scrape rather than a hard recipe.
MAX_LINES = 40
# Anything longer is not an ingredient line.
MAX_LINE_CHARS = 200

_SYSTEM = (
    "You convert a single recipe ingredient line into structured fields. "
    "Return only what the line actually says: never invent a quantity that is "
    "not there, and never translate or rename the food. If the line states no "
    "amount, use null for quantity."
)


def _prompt(lines: list[str]) -> str:
    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(lines))
    return (
        "For each numbered ingredient line, return an object with:\n"
        '  "index": the line number\n'
        '  "quantity": a number, or null if the line states no amount\n'
        '  "unit": the unit as written (cup, g, stick, handful…), or null\n'
        '  "food": the ingredient itself, without the amount or preparation\n'
        '  "note": preparation or qualifiers ("finely chopped", "at room '
        'temperature"), or ""\n'
        '  "confidence": 0.0-1.0, how sure you are\n\n'
        'Reply as {"items": [ ... ]}.\n\n'
        "Lines:\n" + numbered
    )


def _clean(proposal: dict, original: str) -> dict | None:
    """Validate one proposal. Returns None if it cannot be trusted.

    Everything here is a guard against a model that is confidently wrong, which
    is the failure mode that matters: a silently bad quantity changes what
    someone cooks.
    """
    quantity = proposal.get("quantity")
    if quantity is not None:
        try:
            quantity = float(quantity)
        except (TypeError, ValueError):
            return None
        # A negative or absurd amount is a parse failure, not an ingredient.
        if quantity <= 0 or quantity > 10000:
            return None

    raw_unit = proposal.get("unit")
    unit = units.canonical_unit(str(raw_unit)) if raw_unit else None
    # An unrecognised unit is dropped rather than stored: a unit the rest of the
    # app cannot convert or scale is worse than no unit, because it looks
    # structured while behaving like free text.
    food = str(proposal.get("food") or "").strip()[:MAX_LINE_CHARS]
    if not food:
        return None

    try:
        confidence = float(proposal.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "display": original,          # never rewritten
        "quantity": quantity,
        "unit": unit,
        "food": food,
        "note": str(proposal.get("note") or "").strip()[:MAX_LINE_CHARS],
        "confidence": max(0.0, min(1.0, confidence)),
        "source": "ai",
    }


def needs_structuring(display: str) -> bool:
    """True when the deterministic parser got nothing useful from this line."""
    parsed = units.parse_line(display or "")
    return parsed["qty"] is None and parsed["unit"] is None


def structure(displays: list[str], provider=None) -> dict[str, dict]:
    """Propose structure for the lines the parser could not read.

    Returns ``{display_line: proposal}``, containing only lines the model
    answered usefully. An empty dict is a perfectly good outcome and is what
    every caller gets when no provider is configured.
    """
    # Clamped on the way IN as well as out: a scraped "line" can be the whole
    # page up to the fetch cap, and forty of those is a prompt no small model
    # will survive. Every other AI endpoint here clamps its input the same way.
    candidates = [d[:MAX_LINE_CHARS] for d in displays
                  if d and needs_structuring(d)][:MAX_LINES]
    if not candidates:
        return {}

    if provider is None:
        try:
            from .ai.registry import get_provider
            provider = get_provider()
        except Exception as exc:  # noqa: BLE001 - no provider is a normal state
            _LOGGER.info("ingredient structuring skipped: %s", exc)
            return {}

    try:
        raw = provider.complete_json(_prompt(candidates), system=_SYSTEM)
    except Exception as exc:  # noqa: BLE001 - never fail an import for this
        _LOGGER.warning("ingredient structuring failed: %s", exc)
        return {}

    items = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        _LOGGER.warning("ingredient structuring returned no items list")
        return {}

    out: dict[str, dict] = {}
    for item in items[:MAX_LINES]:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        # An index outside what we asked about is discarded — the model must not
        # be able to attach a proposal to a line it was never shown.
        if not 0 <= index < len(candidates):
            continue
        cleaned = _clean(item, candidates[index])
        if cleaned:
            out[candidates[index]] = cleaned
    return out
