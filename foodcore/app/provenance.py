"""Provenance contract — every resolved reference value says where it came from.

⚠️ PLACEHOLDER, written because the owning agent had not landed this module yet.
It is the agreed contract with identical class names, field names, field types,
field ORDER and the `method` comment; the only change is one field per line,
because the one-liner form the contract was written in trips ruff E701/E702 and
this repo's pre-commit hook refuses the edit. Semantically it is the same
dataclass. If the real module lands, delete this file in favour of it — do not
reconcile by editing these dataclasses.

`method` is the spec's evidence ladder, strongest first:
  measured   — physically measured, or an exact definitional conversion
  declared   — stated by a manufacturer/operator (e.g. a package net weight)
  calculated — arithmetic over a measured record (portion weight, density)
  inferred   — deduced from a related record
  estimated  — a convention stood in for missing evidence (size words, counts)
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Provenance:
    source: str
    source_id: str
    retrieved: str
    method: str   # measured | declared | calculated | inferred | estimated
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
