"""The source registry — which dataset snapshot a value came from.

Required by §12 of the Food Intelligence Platform spec. Every value foodcore
serves carries a ``Provenance`` naming a ``source`` and a ``retrieved`` date;
those are only meaningful if something can say, per provider, *which* snapshot
is loaded and under what licence. That is this module.

**Plain frozen dataclasses, deliberately — not ORM models.** foodcore is a
library with no database of its own: it reads a file the host app owns. A host
that wants these rows in its own schema maps ``to_dict()`` into its own table;
a host that does not care ignores them. An ORM model here would drag SQLAlchemy
into every consumer and make the package non-standalone.

There is also no tenant/owner field anywhere in foodcore. What a gram of flour
weighs is identical for every household, so this is reference data; the host
apps stay the system of record for their users' recipes and pantry lots and
keep their own scoping.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .provenance import LICENSES, SOURCES, Provenance


@dataclass(frozen=True)
class SourceRecord:
    """One ingested dataset snapshot."""

    provider: str          # one of provenance.SOURCES
    dataset_version: str   # upstream's own release id, e.g. "April 2025"
    retrieved_at: str      # ISO-8601 date the snapshot was taken
    license: str           # one of provenance.LICENSES
    record_count: int = 0  # how many records it contributed
    notes: str = ""        # ingest notes: filters applied, rows skipped and why

    def __post_init__(self) -> None:
        # The same closed vocabularies ``Provenance`` enforces, imported rather
        # than copied, so a stored value can always be joined back to its
        # snapshot.
        if self.provider not in SOURCES:
            raise ValueError(
                f"provider={self.provider!r} is not one of: {', '.join(sorted(SOURCES))}"
            )
        if self.license not in LICENSES:
            raise ValueError(
                f"license={self.license!r} is not one of: {', '.join(sorted(LICENSES))}"
            )
        if not str(self.dataset_version).strip():
            raise ValueError(
                "dataset_version is required: without it a stored value cannot be "
                "traced to the snapshot it came from"
            )
        if not str(self.retrieved_at).strip():
            raise ValueError("retrieved_at is required (ISO-8601 date)")
        if int(self.record_count) < 0:
            raise ValueError(f"record_count cannot be negative, got {self.record_count}")

    def provenance(self, source_id: str, method: str, version: str,
                   confidence: float) -> Provenance:
        """A ``Provenance`` for a value taken from THIS snapshot.

        Exists so an engine cannot accidentally cite a provider or licence that
        disagrees with the snapshot it actually read.
        """
        return Provenance(
            source=self.provider,
            source_id=source_id,
            retrieved=self.retrieved_at,
            method=method,
            version=version,
            confidence=confidence,
            license=self.license,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "datasetVersion": self.dataset_version,
            "retrievedAt": self.retrieved_at,
            "license": self.license,
            "recordCount": self.record_count,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRecord:
        """Inverse of ``to_dict``, accepting either camelCase or snake_case.

        Both spellings because a host app persisting these rows will have
        serialised them in whichever convention its API uses.
        """
        def pick(*names: str, default: Any = None) -> Any:
            for name in names:
                if name in data:
                    return data[name]
            return default

        return cls(
            provider=pick("provider"),
            dataset_version=pick("datasetVersion", "dataset_version"),
            retrieved_at=pick("retrievedAt", "retrieved_at"),
            license=pick("license"),
            record_count=int(pick("recordCount", "record_count", default=0) or 0),
            notes=pick("notes", default="") or "",
        )
