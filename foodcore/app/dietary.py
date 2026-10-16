"""Dietary screening (FR-07) — versioned rules, uncertainty as an outcome.

Pure: no Flask, no database, no I/O. Same shape as ``app/allergens.py`` on
purpose — four states, evidence always, no boolean without an explicit policy —
because a caller that has learned one API has learned both.

## What this module refuses to do

Claim a diet is satisfied when it cannot know. Two cases, handled honestly:

* **Ingredient-level uncertainty.** The spec's own example is Parmesan, which
  is traditionally made with animal rennet. ``parmesan`` therefore lands in the
  soft class ``animal_rennet``, which ``vegetarian`` lists as *uncertain* — so
  a Parmesan recipe is **never** reported as vegetarian-compatible. The same
  applies to wine and beer (isinglass fining), pastry (lard), and "natural
  flavouring" (source unspecified).
* **Program-level uncertainty.** Whole30 and paleo have no machine-checkable
  authority, and keto/low-carb need nutrient data foodcore does not hold. Those
  diets carry an ``advisory`` string, and :attr:`DietVerdict.certified` is
  False even when nothing conflicted. ``informational`` is stated, not implied.

## Delegation, not duplication

``dairy_free`` and ``gluten_free`` do not re-implement anything: they delegate
to :mod:`app.allergens`, which is the one place the material-boundary rule of
``docs/adr/0001`` lives. That is what makes "almond milk is dairy-free" true
here without a second lexicon that could drift from the first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Sequence

from . import allergens as allergen_engine
from .allergens import (
    AllergenState,
    IngredientRef,
    SensitivityProfile,
    build_phrase_index,
    match_phrases,
    tokenize,
)
from .provenance import Provenance
from .rules import DietaryRuleset, dietary_ruleset

__all__ = [
    "DietState",
    "DietPolicy",
    "DietEvidence",
    "DietVerdict",
    "DietaryScreening",
    "DietaryDecision",
    "CustomDiet",
    "IngredientRef",
    "screen_diets",
    "screen_diet_text",
    "DIET_KEYS",
]


class DietState(Enum):
    """Four outcomes, mirroring :class:`app.allergens.AllergenState`.

    * ``INCOMPATIBLE`` — an ingredient this diet excludes was identified.
    * ``UNCERTAIN`` — an ingredient whose compatibility genuinely depends on
      how it was made (animal rennet, isinglass fining, lard in the pastry).
    * ``UNKNOWN`` — part of the recipe could not be screened at all.
    * ``NO_CONFLICT_IDENTIFIED`` — every line was recognised and nothing
      conflicted. Still not a certification: see :attr:`DietVerdict.certified`.
    """

    INCOMPATIBLE = "incompatible"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"
    NO_CONFLICT_IDENTIFIED = "no_conflict_identified"

    def __str__(self) -> str:  # pragma: no cover - human-facing only
        if self is DietState.NO_CONFLICT_IDENTIFIED:
            return "no conflict identified (not a certification)"
        return self.value


_PRECEDENCE: dict[DietState, int] = {
    DietState.INCOMPATIBLE: 3,
    DietState.UNCERTAIN: 2,
    DietState.UNKNOWN: 1,
    DietState.NO_CONFLICT_IDENTIFIED: 0,
}


class DietPolicy(Enum):
    """``STRICT`` excludes uncertainty; ``PREFERENCE`` only excludes conflicts."""

    STRICT = "strict"
    PREFERENCE = "preference"


_EXCLUDED_UNDER: dict[DietPolicy, frozenset[DietState]] = {
    DietPolicy.STRICT: frozenset({
        DietState.INCOMPATIBLE, DietState.UNCERTAIN, DietState.UNKNOWN,
    }),
    DietPolicy.PREFERENCE: frozenset({DietState.INCOMPATIBLE}),
}

_FROM_ALLERGEN: dict[AllergenState, DietState] = {
    AllergenState.CONTAINS: DietState.INCOMPATIBLE,
    AllergenState.MAY_CONTAIN: DietState.UNCERTAIN,
    AllergenState.UNKNOWN: DietState.UNKNOWN,
    AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED: DietState.NO_CONFLICT_IDENTIFIED,
}

_NO_TRUTH_VALUE = (
    "{cls} has no truth value, on purpose: 'no conflict identified' is not "
    "'compatible'. Call {cls}.{method}(policy) with an explicit DietPolicy "
    "(STRICT excludes uncertain and unknown; PREFERENCE does not)."
)

_CONFIDENCE: dict[str, float] = {
    "excluded_class": 0.9,
    "uncertain_class": 0.5,
    "custom_exclusion": 0.9,
    "allergen_group": 0.85,
    "coverage": 0.5,
    "unscreened_text": 0.0,
}


# A separate evidence type from the allergen engine's, deliberately: the two
# vocabularies are different ("contains milk" vs "incompatible with vegan"),
# and collapsing them would mean one `contributes` field holding two enums.
@dataclass(frozen=True)
class DietEvidence:
    ingredient: str
    matched_term: str
    rule_id: str
    rule_version: str
    match_kind: str
    contributes: DietState
    note: str = ""
    provenance: Provenance | None = None

    def describe(self) -> str:
        where = f"{self.ingredient!r}" if self.ingredient else "the recipe"
        what = f" matched {self.matched_term!r}" if self.matched_term else ""
        why = f" ({self.note})" if self.note else ""
        return (
            f"{where}{what} → {self.contributes.value} "
            f"via {self.rule_id} [{self.rule_version}]{why}"
        )


@dataclass(frozen=True)
class DietVerdict:
    """One diet's state for one recipe. No truth value; policy is required."""

    diet: str
    label: str
    state: DietState
    evidence: tuple[DietEvidence, ...]
    ruleset_version: str
    advisory: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError(
                f"verdict for diet {self.diet!r} has no evidence; a "
                "classification without an evidence trail is a bug"
            )

    def __bool__(self) -> bool:
        raise TypeError(
            _NO_TRUTH_VALUE.format(cls="DietVerdict", method="permitted_under")
        )

    def permitted_under(self, policy: DietPolicy) -> bool:
        if not isinstance(policy, DietPolicy):
            raise TypeError(
                "policy must be a DietPolicy, not "
                f"{type(policy).__name__} — there is no default on purpose"
            )
        return self.state not in _EXCLUDED_UNDER[policy]

    @property
    def certified(self) -> bool:
        """True only when nothing conflicted AND the diet is machine-checkable.

        An advisory diet (whole30, paleo, keto, low-carb, gluten-free
        cross-contact) can never be certified from ingredient text alone, so
        this stays False and the UI must say "informational".
        """
        return self.state is DietState.NO_CONFLICT_IDENTIFIED and not self.advisory

    @property
    def needs_review(self) -> bool:
        return self.state in (DietState.UNCERTAIN, DietState.UNKNOWN)

    def explain(self) -> str:
        head = f"{self.label}: {self.state}"
        if self.advisory:
            head += f" [informational: {self.advisory}]"
        return "\n".join([head] + [f"  - {e.describe()}" for e in self.evidence])

    def __str__(self) -> str:  # pragma: no cover - human-facing only
        return f"{self.label}: {self.state}"


@dataclass(frozen=True)
class DietaryDecision:
    policy: DietPolicy
    allowed: bool
    blocking: tuple[DietVerdict, ...]
    flagged: tuple[DietVerdict, ...]
    unverified: tuple[DietVerdict, ...]
    ruleset_version: str

    def __post_init__(self) -> None:
        if self.allowed != (not self.blocking):
            raise ValueError("decision.allowed disagrees with decision.blocking")

    def __bool__(self) -> bool:
        return self.allowed


@dataclass(frozen=True)
class DietaryScreening:
    verdicts: tuple[DietVerdict, ...]
    ruleset_version: str
    allergen_ruleset_version: str

    def __bool__(self) -> bool:
        raise TypeError(
            _NO_TRUTH_VALUE.format(cls="DietaryScreening", method="decide")
        )

    def verdict(self, diet: str) -> DietVerdict:
        for v in self.verdicts:
            if v.diet == diet:
                return v
        raise KeyError(
            f"{diet!r} was not screened; screened: {[v.diet for v in self.verdicts]}"
        )

    def decide(self, policy: DietPolicy) -> DietaryDecision:
        if not isinstance(policy, DietPolicy):
            raise TypeError(
                "policy must be a DietPolicy, not "
                f"{type(policy).__name__} — there is no default on purpose"
            )
        blocking = tuple(v for v in self.verdicts if not v.permitted_under(policy))
        flagged = tuple(
            v for v in self.verdicts if v.needs_review and v not in blocking
        )
        unverified = tuple(
            v for v in self.verdicts if v.advisory and v not in blocking
        )
        return DietaryDecision(
            policy=policy,
            allowed=not blocking,
            blocking=blocking,
            flagged=flagged,
            unverified=unverified,
            ruleset_version=self.ruleset_version,
        )

    def explain(self) -> str:
        return "\n".join(v.explain() for v in self.verdicts)


@dataclass(frozen=True)
class CustomDiet:
    """A user-defined diet: a key, a label, and the terms it excludes."""

    key: str
    label: str
    exclusions: tuple[str, ...]


DIET_KEYS: tuple[str, ...] = tuple(dietary_ruleset().diets)


# --- the engine --------------------------------------------------------------

_INDEX_CACHE: dict[str, tuple[dict, int]] = {}


def _index_for(ruleset: DietaryRuleset):
    cached = _INDEX_CACHE.get(ruleset.version)
    if cached is None:
        cached = build_phrase_index(ruleset.term_classes)
        _INDEX_CACHE[ruleset.version] = cached
    return cached


def _provenance(ruleset: DietaryRuleset, rule_id: str, kind: str) -> Provenance:
    return Provenance(
        source=ruleset.source,
        source_id=rule_id,
        retrieved=ruleset.retrieved,
        method="inferred",
        version=ruleset.version,
        confidence=_CONFIDENCE.get(kind, 0.5),
        license=ruleset.license,
    )


@dataclass
class _Scan:
    label: str
    hits: list[tuple[str, str]] = field(default_factory=list)  # (class, phrase)


def _scan(
    ing: IngredientRef,
    ruleset: DietaryRuleset,
    path: str = "",
) -> list[_Scan]:
    """Per-line class hits. Labels match the allergen engine's exactly, so the
    two screens can be joined on them without a second ingredient walk."""
    label = f"{path} → {ing.text}" if path else ing.text
    scan = _Scan(label=label)
    index, max_words = _index_for(ruleset)
    matches, _leftover = match_phrases(tokenize(ing.text), index, max_words)
    for match in matches:
        for cls in match.value:  # type: ignore[attr-defined]
            scan.hits.append((cls, match.phrase))
    scans = [scan]
    for component in ing.components:
        scans.extend(_scan(component, ruleset, path=label))
    return scans


def screen_diets(
    ingredients: Iterable[IngredientRef | str],
    diets: Sequence[str] = DIET_KEYS,
    *,
    custom: CustomDiet | None = None,
    ruleset_version: str | None = None,
    allergen_ruleset_version: str | None = None,
) -> DietaryScreening:
    """Screen ``ingredients`` against ``diets``.

    Returns a :class:`DietaryScreening`, which has no truth value. To get a
    boolean, call :meth:`DietaryScreening.decide` with an explicit
    :class:`DietPolicy`.
    """
    ruleset = dietary_ruleset(ruleset_version)
    wanted = list(diets)
    if custom:
        ruleset = ruleset.with_custom_exclusions(
            custom.key, custom.label, custom.exclusions
        )
        if custom.key not in wanted:
            wanted.append(custom.key)

    refs = [IngredientRef(text=i) if isinstance(i, str) else i for i in ingredients]
    for key in wanted:
        if key not in ruleset.diets:
            raise ValueError(
                f"unknown diet {key!r}; known: {sorted(ruleset.diets)}"
            )

    scans: list[_Scan] = []
    for ref in refs:
        scans.extend(_scan(ref, ruleset))

    # One allergen screening serves two purposes: the delegated groups
    # (dairy-free, gluten-free) and the coverage/`unknown` signal for every
    # diet. Running it with an empty allergen list still yields coverage.
    groups: list[str] = []
    for key in wanted:
        for group in ruleset.diets[key].allergen_groups:
            if group not in groups:
                groups.append(group)
    ascreen = allergen_engine.screen(
        refs,
        SensitivityProfile(allergens=tuple(groups)),
        ruleset_version=allergen_ruleset_version,
    )
    unscreened: Mapping[str, tuple[str, ...]] = {
        c.ingredient: c.unscreened for c in ascreen.coverage
    }
    averdicts = {v.allergen: v for v in ascreen.verdicts}

    verdicts = []
    for key in wanted:
        rule = ruleset.diets[key]
        evidence: list[DietEvidence] = []
        # Only a positive conflict makes an unscreened line uninteresting. An
        # `uncertain` line that we ALSO could not fully read keeps both pieces
        # of evidence, so the explanation names both problems.
        lines_with_conflict: set[str] = set()

        for scan in scans:
            for cls, phrase in scan.hits:
                if cls in rule.incompatible:
                    kind, state = "excluded_class", DietState.INCOMPATIBLE
                elif cls in rule.uncertain:
                    kind, state = "uncertain_class", DietState.UNCERTAIN
                else:
                    continue
                if cls.startswith("custom:"):
                    kind = "custom_exclusion"
                rule_id = f"{key}.{kind}.{cls}.{phrase}"
                evidence.append(DietEvidence(
                    ingredient=scan.label,
                    matched_term=phrase,
                    rule_id=rule_id,
                    rule_version=ruleset.version,
                    match_kind=kind,
                    contributes=state,
                    note=ruleset.term_notes.get(phrase, f"{phrase!r} is {cls}"),
                    provenance=_provenance(ruleset, rule_id, kind),
                ))
                if state is DietState.INCOMPATIBLE:
                    lines_with_conflict.add(scan.label)

        for group in rule.allergen_groups:
            av = averdicts.get(group)
            if av is None:
                continue
            state = _FROM_ALLERGEN[av.state]
            if state is DietState.NO_CONFLICT_IDENTIFIED:
                continue
            for ev in av.evidence:
                evidence.append(DietEvidence(
                    ingredient=ev.ingredient,
                    matched_term=ev.matched_term,
                    rule_id=f"{key}.allergen_group.{ev.rule_id}",
                    rule_version=f"{ruleset.version}+{ev.rule_version}",
                    match_kind="allergen_group",
                    contributes=_FROM_ALLERGEN[ev.contributes],
                    note=(f"delegated to the allergen engine "
                          f"({av.label}): {ev.note}" if ev.note else
                          f"delegated to the allergen engine ({av.label})"),
                    provenance=ev.provenance,
                ))
                if ev.ingredient and ev.contributes is AllergenState.CONTAINS:
                    lines_with_conflict.add(ev.ingredient)

        # Lines we could not screen are `unknown` for every diet that got no
        # other evidence from them — the dietary mirror of the allergen rule.
        for scan in scans:
            reasons = unscreened.get(scan.label, ())
            if not reasons or scan.label in lines_with_conflict:
                continue
            for reason in reasons:
                rule_id = f"{key}.unscreened_text"
                evidence.append(DietEvidence(
                    ingredient=scan.label,
                    matched_term="",
                    rule_id=rule_id,
                    rule_version=ruleset.version,
                    match_kind="unscreened_text",
                    contributes=DietState.UNKNOWN,
                    note=reason,
                    provenance=_provenance(ruleset, rule_id, "unscreened_text"),
                ))

        if evidence:
            state = max(
                (ev.contributes for ev in evidence), key=lambda s: _PRECEDENCE[s]
            )
        else:
            state = DietState.NO_CONFLICT_IDENTIFIED
            rule_id = f"{key}.coverage"
            evidence = [DietEvidence(
                ingredient="",
                matched_term="",
                rule_id=rule_id,
                rule_version=ruleset.version,
                match_kind="coverage",
                contributes=state,
                note=(
                    f"{len(scans)} ingredient line(s) screened against "
                    f"{ruleset.version}; every line was recognised and none "
                    f"conflicted with {rule.label}. Not a certification."
                ),
                provenance=_provenance(ruleset, rule_id, "coverage"),
            )]

        verdicts.append(DietVerdict(
            diet=key,
            label=rule.label,
            state=state,
            evidence=tuple(evidence),
            ruleset_version=ruleset.version,
            advisory=rule.advisory,
        ))

    return DietaryScreening(
        verdicts=tuple(verdicts),
        ruleset_version=ruleset.version,
        allergen_ruleset_version=ascreen.ruleset_version,
    )


def screen_diet_text(
    text: str,
    diets: Sequence[str] = DIET_KEYS,
    **kwargs,
) -> DietaryScreening:
    """Screen a single ingredient line. Convenience over :func:`screen_diets`."""
    return screen_diets([IngredientRef(text=text)], diets, **kwargs)
