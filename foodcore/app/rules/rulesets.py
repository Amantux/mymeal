"""Rule *loading* — turns the versioned data modules into frozen rulesets.

Kept apart from the data (``allergens_v1.py``, ``dietary_v1.py``) and from the
engines (``app/allergens.py``, ``app/dietary.py``) so that each layer has one
job: data states facts, this module composes and validates them, the engines
decide. Adding ``allergens_v2`` is a new data module plus one registry line;
nothing in the engines changes, and old evidence keeps naming the version it
was produced under.

The composition order is load-bearing:

1. ``TERMS`` → ``contains`` hits, carrying their match kind.
2. ``MAY_CONTAIN_TERMS`` → ``may_contain`` hits, merged on top.
3. ``MATERIAL_PHRASES`` → **replace** whatever a phrase had accumulated. This
   is the material-boundary rule of ``docs/adr/0001``: ``almond milk`` is not
   milk-plus-almond, it is almond. Replacement, not merging, is what makes that
   true.
4. ``KNOWN_FREE`` → an empty hit tuple, only for phrases nothing else claimed.
   An empty tuple means "recognised, nothing matched", which is evidence of
   screening and **not** a safety claim.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from . import allergens_v1, dietary_v1, text_v1

CUSTOM_PREFIX = "custom:"


@dataclass(frozen=True)
class PhraseHit:
    """One allergen consequence of one matched phrase."""

    allergen: str
    kind: str  # canonical | alias | derivative | material_boundary | ambiguous | custom
    state: str  # contains | may_contain
    note: str = ""


@dataclass(frozen=True)
class AllergenRuleset:
    version: str
    license: str
    source: str
    labels: Mapping[str, str]
    major: frozenset[str]
    implies: Mapping[str, tuple[str, ...]]
    phrases: Mapping[str, tuple[PhraseHit, ...]]
    opaque: frozenset[str]
    precaution_markers: tuple[str, ...]
    free_from: Mapping[str, tuple[str, ...]]
    vegan_claim_allergens: tuple[str, ...]
    stop_words: frozenset[str]
    retrieved: str
    max_phrase_words: int

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self.labels)

    def label(self, allergen: str) -> str:
        if allergen.startswith(CUSTOM_PREFIX):
            return allergen[len(CUSTOM_PREFIX):]
        return self.labels.get(allergen, allergen)

    def with_custom_exclusions(self, terms: tuple[str, ...]) -> AllergenRuleset:
        """A derived ruleset that also screens arbitrary user-named terms.

        The derived version string records the customisation, so a stored
        verdict can never be mistaken for one produced by the base ruleset.
        """
        clean = tuple(sorted({t.strip().lower() for t in terms if t and t.strip()}))
        if not clean:
            return self
        phrases = dict(self.phrases)
        labels = dict(self.labels)
        for term in clean:
            key = CUSTOM_PREFIX + term
            labels[key] = term
            hit = PhraseHit(key, "custom", "contains", "user-named exclusion")
            phrases[term] = (*phrases.get(term, ()), hit)
        return replace(
            self,
            version=f"{self.version}+custom({','.join(clean)})",
            labels=MappingProxyType(labels),
            phrases=MappingProxyType(phrases),
            max_phrase_words=max(
                self.max_phrase_words, *(len(t.split()) for t in clean)
            ),
        )


@dataclass(frozen=True)
class DietRule:
    key: str
    label: str
    incompatible: tuple[str, ...]
    uncertain: tuple[str, ...]
    allergen_groups: tuple[str, ...]
    advisory: str | None


@dataclass(frozen=True)
class DietaryRuleset:
    version: str
    license: str
    source: str
    diets: Mapping[str, DietRule]
    term_classes: Mapping[str, tuple[str, ...]]
    term_notes: Mapping[str, str]
    stop_words: frozenset[str]
    retrieved: str
    max_phrase_words: int

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self.diets)

    def with_custom_exclusions(
        self, key: str, label: str, terms: tuple[str, ...]
    ) -> DietaryRuleset:
        """A derived ruleset carrying one extra, user-defined diet."""
        clean = tuple(sorted({t.strip().lower() for t in terms if t and t.strip()}))
        if not clean:
            return self
        cls = f"{CUSTOM_PREFIX}{key}"
        term_classes = {t: tuple(c) for t, c in self.term_classes.items()}
        for term in clean:
            term_classes[term] = (*term_classes.get(term, ()), cls)
        diets = dict(self.diets)
        diets[key] = DietRule(key, label, (cls,), (), (), None)
        return replace(
            self,
            version=f"{self.version}+custom({key}:{','.join(clean)})",
            diets=MappingProxyType(diets),
            term_classes=MappingProxyType(term_classes),
            max_phrase_words=max(
                self.max_phrase_words, *(len(t.split()) for t in clean)
            ),
        )


def _build_allergens(mod) -> AllergenRuleset:
    phrases: dict[str, tuple[PhraseHit, ...]] = {}

    def add(phrase: str, hit: PhraseHit) -> None:
        phrases[phrase] = (*phrases.get(phrase, ()), hit)

    for allergen, by_kind in mod.TERMS.items():
        if allergen not in mod.ALLERGENS:
            raise ValueError(f"TERMS names unknown allergen {allergen!r}")
        for kind, terms in by_kind.items():
            for term in terms:
                add(term, PhraseHit(allergen, kind, "contains"))

    for allergen, entries in mod.MAY_CONTAIN_TERMS.items():
        if allergen not in mod.ALLERGENS:
            raise ValueError(f"MAY_CONTAIN_TERMS names unknown allergen {allergen!r}")
        for term, why in entries:
            if not why:
                raise ValueError(f"may-contain term {term!r} has no reason")
            add(term, PhraseHit(allergen, "ambiguous", "may_contain", why))

    # Replacement, not merging — see the module docstring.
    for phrase, consequences in mod.MATERIAL_PHRASES.items():
        hits = []
        for allergen, state in consequences:
            if allergen not in mod.ALLERGENS:
                raise ValueError(f"MATERIAL_PHRASES names unknown allergen {allergen!r}")
            if state not in ("contains", "may_contain"):
                raise ValueError(f"MATERIAL_PHRASES state {state!r} is not a state")
            hits.append(
                PhraseHit(allergen, "material_boundary", state,
                          "distinct material from its head word (ADR-0001)")
            )
        phrases[phrase] = tuple(hits)

    for phrase in mod.KNOWN_FREE:
        phrases.setdefault(phrase, ())

    for phrase in mod.OPAQUE_TERMS:
        phrases.setdefault(phrase, ())

    return AllergenRuleset(
        version=mod.VERSION,
        license=mod.LICENSE,
        source=mod.SOURCE,
        labels=MappingProxyType({k: v[0] for k, v in mod.ALLERGENS.items()}),
        major=frozenset(k for k, v in mod.ALLERGENS.items() if v[1]),
        implies=MappingProxyType({k: v[2] for k, v in mod.ALLERGENS.items()}),
        phrases=MappingProxyType(phrases),
        opaque=frozenset(mod.OPAQUE_TERMS),
        precaution_markers=tuple(mod.PRECAUTION_MARKERS),
        free_from=MappingProxyType(dict(mod.FREE_FROM_CLAIMS)),
        vegan_claim_allergens=tuple(mod.VEGAN_CLAIM_ALLERGENS),
        stop_words=text_v1.STOP_WORDS,
        retrieved=mod.RETRIEVED,
        max_phrase_words=max(len(p.split()) for p in phrases),
    )


def _build_dietary(mod) -> DietaryRuleset:
    term_classes: dict[str, tuple[str, ...]] = {}
    for cls, terms in mod.CLASS_TERMS.items():
        for term in terms:
            term_classes[term] = (*term_classes.get(term, ()), cls)

    # A soft class without an explanation is a bug: the user is told "we are not
    # sure" and must be told why.
    for cls in mod.SOFT_CLASSES:
        for term in mod.CLASS_TERMS[cls]:
            if not mod.TERM_NOTES.get(term):
                raise ValueError(f"soft-class term {term!r} ({cls}) has no TERM_NOTES entry")

    diets = {}
    known = set(mod.CLASS_TERMS)
    for key, spec in mod.DIETS.items():
        incompatible = tuple(spec["incompatible"])  # type: ignore[arg-type]
        uncertain = tuple(spec["uncertain"])  # type: ignore[arg-type]
        for cls in incompatible + uncertain:
            if cls not in known:
                raise ValueError(f"diet {key!r} names unknown class {cls!r}")
        diets[key] = DietRule(
            key=key,
            label=str(spec["label"]),
            incompatible=incompatible,
            uncertain=uncertain,
            allergen_groups=tuple(spec["allergen_groups"]),  # type: ignore[arg-type]
            advisory=spec["advisory"],  # type: ignore[arg-type]
        )

    return DietaryRuleset(
        version=mod.VERSION,
        license=mod.LICENSE,
        source=mod.SOURCE,
        diets=MappingProxyType(diets),
        term_classes=MappingProxyType(term_classes),
        term_notes=MappingProxyType(dict(mod.TERM_NOTES)),
        stop_words=text_v1.STOP_WORDS,
        retrieved=mod.RETRIEVED,
        max_phrase_words=max(len(t.split()) for t in term_classes),
    )


ALLERGEN_RULESETS: Mapping[str, AllergenRuleset] = MappingProxyType(
    {allergens_v1.VERSION: _build_allergens(allergens_v1)}
)
DIETARY_RULESETS: Mapping[str, DietaryRuleset] = MappingProxyType(
    {dietary_v1.VERSION: _build_dietary(dietary_v1)}
)

CURRENT_ALLERGEN_VERSION = allergens_v1.VERSION
CURRENT_DIETARY_VERSION = dietary_v1.VERSION


def allergen_ruleset(version: str | None = None) -> AllergenRuleset:
    """The allergen ruleset for ``version`` (default: current). Never guesses."""
    key = version or CURRENT_ALLERGEN_VERSION
    try:
        return ALLERGEN_RULESETS[key]
    except KeyError:
        raise ValueError(
            f"unknown allergen ruleset {key!r}; known: {sorted(ALLERGEN_RULESETS)}"
        ) from None


def dietary_ruleset(version: str | None = None) -> DietaryRuleset:
    """The dietary ruleset for ``version`` (default: current). Never guesses."""
    key = version or CURRENT_DIETARY_VERSION
    try:
        return DIETARY_RULESETS[key]
    except KeyError:
        raise ValueError(
            f"unknown dietary ruleset {key!r}; known: {sorted(DIETARY_RULESETS)}"
        ) from None
