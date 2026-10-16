"""Provenance value objects — the spec's §5.3 model, adopted verbatim.

⚠️ PLACEHOLDER. A sibling agent owns this file. It was absent from this
worktree when ``nutrition.py`` was written, so this is a minimal copy of the
agreed contract so the module could be built and tested. If the sibling's
version lands, KEEP THEIRS and delete this file — the contractual part is the
field names, their order and their types, which are reproduced exactly:

    Provenance: source, source_id, retrieved, method, version, confidence, license
    Resolved:   value, unit, provenance
    Unresolved: reason, detail

``method`` is a closed vocabulary: measured | declared | calculated | inferred
| estimated. ``retrieved`` is an ISO-8601 timestamp string (nutrition.py takes
the lexicographic minimum across inputs, which is only correct for ISO-8601).
"""
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
