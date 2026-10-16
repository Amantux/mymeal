"""Provenance contract — the spec's §5.3 model, shared by every foodcore value.

⚠️ **PLACEHOLDER COPY.** This file is owned by the provenance agent. It was
absent when the allergen/dietary engine was written, so a minimal copy of the
agreed contract was committed here to unblock. The field names, types and order
are exactly the agreed contract; the only deviation is formatting (the contract
was written with semicolon-separated fields, which the repo's ruff rule set
rejects as E702). **If the owning agent's version lands, delete this file — do
not merge the two.** Nothing in allergens.py/dietary.py adds to this contract;
they only construct ``Provenance``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provenance:
    source: str
    source_id: str
    retrieved: str
    method: str  # measured | declared | calculated | inferred | estimated
    version: str
    confidence: float
    license: str


@dataclass(frozen=True)
class Resolved:
    value: float
    unit: str
    provenance: Provenance


@dataclass(frozen=True)
class Unresolved:
    reason: str
    detail: str
