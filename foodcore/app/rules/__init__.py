"""Versioned rule data for the safety engines.

``allergens_v1`` / ``dietary_v1`` are pure data. ``rulesets`` composes and
validates them into frozen objects. Import from here.
"""
from __future__ import annotations

from .rulesets import (
    ALLERGEN_RULESETS,
    CURRENT_ALLERGEN_VERSION,
    CURRENT_DIETARY_VERSION,
    DIETARY_RULESETS,
    AllergenRuleset,
    DietaryRuleset,
    DietRule,
    PhraseHit,
    allergen_ruleset,
    dietary_ruleset,
)

__all__ = [
    "ALLERGEN_RULESETS",
    "CURRENT_ALLERGEN_VERSION",
    "CURRENT_DIETARY_VERSION",
    "DIETARY_RULESETS",
    "AllergenRuleset",
    "DietRule",
    "DietaryRuleset",
    "PhraseHit",
    "allergen_ruleset",
    "dietary_ruleset",
]
