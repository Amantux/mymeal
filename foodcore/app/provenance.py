"""The provenance contract — the one module every engine in foodcore depends on.

This is adopted near-verbatim from the Food Intelligence Platform spec (§5.3),
which docs/food-intelligence-platform.md calls "the best part of the document".
Three rules make it worth the ceremony:

1. **Every engine returns ``Resolved | Unresolved``.** Never ``None``, and never
   ``0`` as a stand-in for unknown. Conflating "zero grams of fat" with "we
   don't know the fat content" is the single defect this whole service exists to
   prevent: it is how a nutrition panel ends up quietly lying, and how an
   allergen filter ends up calling an unknown food safe.
2. **``Unresolved`` is a legitimate answer, not an error.** Refusing to
   fabricate a conversion for "1 can of tomatoes" is correct behaviour; it
   carries a machine-readable ``reason`` so callers can branch, and a
   human-readable ``detail`` saying what would fix it.
3. **Vocabularies are enforced, not documented.** ``source``, ``method`` and
   ``license`` are closed sets. A typo'd ``method="mesured"`` would otherwise
   propagate into stored records and be indistinguishable from real data later,
   so construction fails loudly instead.

Values are frozen dataclasses: a provenance record describes a measurement that
already happened, so mutating one is always a bug.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

# --------------------------------------------------------------------------
# Closed vocabularies
# --------------------------------------------------------------------------

#: Where a value came from. ``internal`` covers foodcore's own rules/defaults.
SOURCES = frozenset({"usda", "off", "foodon", "internal"})

#: How a value came to be known, ordered weakest-to-strongest evidence:
#:   measured   — somebody physically measured it (USDA lab analysis)
#:   declared   — a party asserted it (manufacturer label, OFF submission)
#:   calculated — exact arithmetic over resolved inputs (per-100g x grams)
#:   inferred   — a deterministic rule over related data (density of a class)
#:   estimated  — a model or heuristic guessed it (LLM, nearest-neighbour)
#: Consumers may legitimately refuse to display or trust ``estimated``, which is
#: only possible because the distinction is recorded at the source.
METHODS = frozenset({"measured", "declared", "calculated", "inferred", "estimated"})

#: Licences of the datasets foodcore is allowed to ingest. ODbL (Open Food
#: Facts) carries share-alike/attribution duties that CC0 (USDA) does not, so
#: the licence has to travel WITH the value — a downstream app cannot work out
#: its attribution obligations from an aggregate number.
LICENSES = frozenset({"CC0-1.0", "ODbL-1.0", "CC-BY-4.0", "internal"})


class VocabularyError(ValueError):
    """A provenance field was given a value outside its closed vocabulary."""


def _require(value: Any, allowed: frozenset[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise VocabularyError(
            f"provenance {field}={value!r} is not one of: "
            f"{', '.join(sorted(allowed))}"
        )
    return value


# --------------------------------------------------------------------------
# The records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Provenance:
    source: str       # "usda" | "off" | "foodon" | "internal"
    source_id: str    # FDC id, GTIN, rule id — empty for internal defaults
    retrieved: str    # ISO-8601 date of the dataset snapshot
    method: str       # measured | declared | calculated | inferred | estimated
    version: str      # transformation or rule version, e.g. "convert/1"
    confidence: float  # 0.0-1.0
    license: str      # "CC0-1.0" | "ODbL-1.0" | "CC-BY-4.0" | "internal"

    def __post_init__(self) -> None:
        _require(self.source, SOURCES, "source")
        _require(self.method, METHODS, "method")
        _require(self.license, LICENSES, "license")
        if not isinstance(self.confidence, (int, float)) or isinstance(
            self.confidence, bool
        ):
            raise ValueError(f"provenance confidence must be a number, got {self.confidence!r}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError(
                f"provenance confidence must be between 0.0 and 1.0, got {self.confidence!r}"
            )
        if not str(self.version).strip():
            raise ValueError(
                "provenance version is required: it is what lets a stored value be "
                'traced to the rule that produced it (e.g. "convert/1")'
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "sourceId": self.source_id,
            "retrieved": self.retrieved,
            "method": self.method,
            "version": self.version,
            "confidence": float(self.confidence),
            "license": self.license,
        }


@dataclass(frozen=True)
class Resolved:    # a value we can stand behind
    value: float
    unit: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, Provenance):
            raise ValueError(
                "Resolved requires a Provenance — an unattributed value is exactly "
                "what this service exists to stop producing"
            )
        if not str(self.unit).strip():
            raise ValueError("Resolved requires a unit; a bare number is not a quantity")

    @property
    def resolved(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        # ``status`` is the discriminator callers (and the REST layer) branch on,
        # so a JSON consumer never has to infer resolvedness from which keys
        # happen to be present.
        return {
            "status": "resolved",
            "value": float(self.value),
            "unit": self.unit,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class Unresolved:  # refusing to fabricate — NOT an error, a legitimate answer
    reason: str    # machine-readable code, e.g. "no_portion_data"
    detail: str    # one human sentence explaining what would fix it

    def __post_init__(self) -> None:
        if not str(self.reason).strip():
            raise ValueError(
                "Unresolved requires a machine-readable reason code so callers can "
                'branch on it (e.g. "no_portion_data")'
            )
        if not str(self.detail).strip():
            raise ValueError(
                "Unresolved requires a human-readable detail saying what would fix "
                "it; a bare refusal is not actionable"
            )

    @property
    def resolved(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"status": "unresolved", "reason": self.reason, "detail": self.detail}


#: The return type of EVERY engine in this service. Annotate with this rather
#: than ``Resolved | None``, which reintroduces the conflation.
Outcome = Union[Resolved, Unresolved]
