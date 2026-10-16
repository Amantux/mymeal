"""foodcore — shared food reference logic for myMeal and Edibl.

A **library**, not a service. There is no container to install, no database
server, no HTTP port and no discovery: a host app installs this package and
calls it in-process. Both apps need the same unit conversions, the same USDA
portion weights and the same allergen rules, and the estate's cross-app rule is
that shared *policy* lives in exactly one place.

The deal with a host app, in four sentences:

1. **Every answer is ``Resolved`` or ``Unresolved``** (see ``provenance``).
   Never ``None``, and never ``0`` standing in for unknown — conflating "zero
   grams of fat" with "we don't know" is the defect this package exists to
   prevent.
2. **The host owns the data file.** foodcore never looks in a hardcoded
   location: you pass a path (or set ``FOODCORE_DATASET``), and a tiny bundled
   fallback keeps ``import foodcore`` working out of the box.
3. **The host owns persistence.** Source records are plain dataclasses; store
   them in your own schema if you want them.
4. **Zero runtime dependencies.** Standard library only — no Flask, no
   SQLAlchemy, nothing reaching into a host app. That is what makes lifting
   this directory into its own repository a no-op.

Public surface (everything else is private)::

    from foodcore import (
        Provenance, Resolved, Unresolved, Outcome,   # the contract
        SourceRecord,                                # dataset registry, plain data
        Dataset, load_dataset, build_dataset,        # reference data
        rule_version, __version__,                   # versioning
    )
    from foodcore import convert, nutrition, allergens, dietary  # the engines

**Import it as ``foodcore``, never as ``app``.** The directory is named ``app``
for historical reasons and is installed under the name ``foodcore``; importing
both names would load the module twice and give you two incompatible copies of
``Provenance``. See ``README.md``.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from .datasets import Dataset, FoodRow, build_dataset, load_dataset
from .provenance import (
    LICENSES,
    METHODS,
    SOURCES,
    Outcome,
    Provenance,
    Resolved,
    Unresolved,
    VocabularyError,
)
from .registry import SourceRecord

#: The package version. Bumped on release; ``rule_version`` derives the
#: algorithm versions that end up in ``Provenance.version`` from it, so a host
#: app can always tell which foodcore computed a value it stored.
__version__ = "0.1.0"

#: The rule epoch — the leading number in every ``Provenance.version`` string.
#: It is NOT the package version: a patch release that fixes a typo must not
#: invalidate stored provenance. Bump it only when an algorithm's OUTPUT
#: changes, and then record what changed in the README's history section.
RULE_EPOCH = 1

#: Dataset files declare the schema they were built with; a Dataset refuses to
#: open one from a future schema rather than mis-reading it.
DATASET_SCHEMA_VERSION = 1


def rule_version(kind: str) -> str:
    """The string an engine puts in ``Provenance.version``, e.g. ``convert/1``.

    Derived from ``RULE_EPOCH`` rather than hardcoded at each call site, so the
    whole package moves together and a stored value can be matched against the
    rules that produced it.
    """
    if not kind or "/" in kind:
        raise ValueError(f"rule kind must be a bare name, got {kind!r}")
    return f"{kind}/{RULE_EPOCH}"


# The engines live in sibling modules. They are exposed lazily (PEP 562) so
# that ``import foodcore`` works before they all exist, and so a host app that
# only needs conversions does not pay for the rest.
_ENGINES = ("convert", "nutrition", "allergens", "dietary")

if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from . import allergens, convert, dietary, nutrition  # noqa: F401


def __getattr__(name: str) -> Any:
    if name in _ENGINES:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(_ENGINES))


__all__ = [
    # the contract
    "Provenance",
    "Resolved",
    "Unresolved",
    "Outcome",
    "VocabularyError",
    "SOURCES",
    "METHODS",
    "LICENSES",
    # dataset registry (plain data — the host persists it if it wants to)
    "SourceRecord",
    # reference data
    "Dataset",
    "FoodRow",
    "load_dataset",
    "build_dataset",
    # versioning
    "__version__",
    "RULE_EPOCH",
    "DATASET_SCHEMA_VERSION",
    "rule_version",
]
