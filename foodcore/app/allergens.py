"""Allergen screening (FR-06) — four states, evidence always, fail-closed.

Pure: no Flask, no database, no I/O. Everything here operates on plain data, so
it is unit-testable and safe to call from any surface (REST, MCP, HA).

## Why the return type looks like this

The failure this module exists to prevent is one line of calling code::

    if verdict.state != AllergenState.CONTAINS:
        show_recipe()          # WRONG. "unknown" is not "safe".

``Food.allergens`` in myMeal is an empty JSON list for anything a user typed,
and the moment a "hide recipes containing nuts" filter ships, ``[] == safe`` is
the default reading. So the API is shaped to make the unsafe reading awkward:

1. **There is no boolean without a policy.** The only way to get a ``bool`` is
   :meth:`AllergenVerdict.permitted_under` / :meth:`AllergenScreening.decide`,
   and ``policy`` is a **required positional argument with no default**. You
   cannot accidentally get the lenient reading; you have to name it, in code,
   where a reviewer can see it.
2. **Verdicts and screenings have no truth value.** ``bool(verdict)`` and
   ``if screening:`` raise :class:`TypeError` with a message explaining what to
   call instead. The shortest wrong thing to write does not compile-by-accident,
   it blows up in the first test.
3. **Nothing in this module is named ``safe``, ``ok``, ``passes`` or ``free``.**
   The fourth state is spelled ``NO_KNOWN_INGREDIENT_IDENTIFIED`` because that
   is what it means: we found nothing matching *in the data we have*. Its
   ``str()`` says so out loud.
4. **Under the strict policy, ``unknown`` is excluded.** Not flagged, not
   passed-with-a-warning: excluded, exactly like ``contains``. That is the rule
   that protects someone with a real allergy, and ``test_allergens.py``
   mutation-checks it.

## Evidence

Every verdict carries ``evidence``: which ingredient, which rule, which rule
version, what text matched, and what that match contributed. A verdict with no
evidence is a bug and the constructor raises. Even the fourth state carries
evidence — a coverage record stating how many ingredients were screened against
which ruleset — because "we looked and found nothing" is only meaningful if you
can see what "looked" meant.

## The material boundary

``docs/adr/0001`` says a substring match must never cross a material boundary:
"almond milk" is not milk. That rule is enforced here by construction, not by a
special case — phrase matching is **longest-first and consuming**, and
``MATERIAL_PHRASES`` in the rule data holds the compounds whose head word
belongs to another material. ``almond milk`` is a longer phrase than ``milk``,
so it matches first, consumes both tokens, and ``milk`` never fires.

The failure direction is deliberately the opposite of
``food_resolve.normalize``'s. There, an unknown phrase is left alone ("didn't
normalise"). Here, an unlisted compound such as "tiger nut milk" still matches
``milk`` and is excluded from a milk-avoiding search. A missing rule costs a
false exclusion; it must never cost a false clearance.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Sequence

from .provenance import Provenance
from .rules import AllergenRuleset, allergen_ruleset
from .rules.rulesets import CUSTOM_PREFIX, PhraseHit

__all__ = [
    "AllergenState",
    "AvoidancePolicy",
    "Evidence",
    "IngredientRef",
    "SensitivityProfile",
    "AllergenVerdict",
    "AllergenScreening",
    "ScreeningDecision",
    "US_MAJOR_ALLERGENS",
    "screen",
    "screen_text",
    "resolve_allergen_key",
    "normalize_text",
    "tokenize",
    "build_phrase_index",
    "match_phrases",
    "PhraseMatch",
]


class AllergenState(Enum):
    """The four outcomes. The last two are different and the difference matters.

    * ``CONTAINS`` — positive evidence the allergen is present.
    * ``MAY_CONTAIN`` — precautionary, ambiguous, optional or claim-downgraded
      evidence. Presence is plausible and unresolved.
    * ``UNKNOWN`` — we could not screen some of this. An unrecognised branded
      item, an opaque composite ("sauce"), text we did not understand. **Not a
      clearance.**
    * ``NO_KNOWN_INGREDIENT_IDENTIFIED`` — every ingredient was recognised and
      none of them matched a rule for this allergen. This is the strongest
      statement the engine can make, and it is still **not proof of safety**:
      it is bounded by the rule data and by what the recipe actually declares.
    """

    CONTAINS = "contains"
    MAY_CONTAIN = "may_contain"
    UNKNOWN = "unknown"
    NO_KNOWN_INGREDIENT_IDENTIFIED = "no_known_ingredient_identified"

    def __str__(self) -> str:  # pragma: no cover - human-facing only
        if self is AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED:
            return "no known ingredient identified (not a safety claim)"
        return self.value.replace("_", " ")


# Which evidence wins when several ingredients disagree. Strictly ordered, and
# `UNKNOWN` deliberately outranks "found nothing": one opaque ingredient makes
# the whole recipe unscreened, it does not get averaged away.
_PRECEDENCE: dict[AllergenState, int] = {
    AllergenState.CONTAINS: 3,
    AllergenState.MAY_CONTAIN: 2,
    AllergenState.UNKNOWN: 1,
    AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED: 0,
}


class AvoidancePolicy(Enum):
    """How much uncertainty a caller is willing to accept.

    ``STRICT`` is a hard constraint — a medical allergy. It excludes
    ``CONTAINS``, ``MAY_CONTAIN`` **and** ``UNKNOWN``. Unknown is never
    silently passed.

    ``PREFERENCE`` is a dislike or a soft avoidance. It excludes only
    ``CONTAINS`` and reports everything uncertain in
    :attr:`ScreeningDecision.flagged`, so the UI can still say so.
    """

    STRICT = "strict"
    PREFERENCE = "preference"


_EXCLUDED_UNDER: dict[AvoidancePolicy, frozenset[AllergenState]] = {
    AvoidancePolicy.STRICT: frozenset({
        AllergenState.CONTAINS,
        AllergenState.MAY_CONTAIN,
        AllergenState.UNKNOWN,
    }),
    AvoidancePolicy.PREFERENCE: frozenset({AllergenState.CONTAINS}),
}

_NO_TRUTH_VALUE = (
    "{cls} has no truth value, on purpose: 'not CONTAINS' is not 'safe'. "
    "Call {cls}.{method}(policy) with an explicit AvoidancePolicy "
    "(STRICT excludes unknown; PREFERENCE does not)."
)

# How confident a kind of match is, and how the provenance model describes it.
# `declared` = someone stated it (a label, a free-from claim); `inferred` = we
# worked it out from the rule data.
_CONFIDENCE: dict[str, float] = {
    "declared_label": 1.0,
    "may_contain_label": 0.9,
    "canonical": 0.95,
    "alias": 0.9,
    "material_boundary": 0.9,
    "custom": 0.9,
    "derivative": 0.85,
    "precautionary_statement": 0.6,
    "ambiguous": 0.5,
    "declared_free_from": 0.5,
    "optional_ingredient": 0.5,
    "coverage": 0.5,
    "opaque_composite": 0.0,
    "unrecognised_text": 0.0,
}
_DECLARED_KINDS = frozenset({
    "declared_label", "may_contain_label", "declared_free_from",
})


# --- text matching (shared with dietary.py) ---------------------------------

_NONWORD_RE = re.compile(r"[^a-z0-9]+")
_NUMERIC_RE = re.compile(r"^[0-9]+([./][0-9]+)?$")
# "200ml" -> "200 ml". Digit-then-letter only: the reverse direction would
# shred additive codes and vitamin names ("e220", "vitamin d3").
_GLUED_UNIT_RE = re.compile(r"(?<=[0-9])(?=[a-z])")


def normalize_text(raw: str) -> str:
    """Lowercase, accent-folded, punctuation-to-space.

    Deliberately different from ``food_resolve.normalize_text``, which drops
    everything after the first comma and strips parentheticals: those are
    preparation noise when you are asking "which food is this?", but they are
    exactly where allergen information hides ("margarine (contains milk)",
    "bread, may contain sesame"). Nothing is discarded here.
    """
    low = (raw or "").strip().lower()
    low = "".join(
        c for c in unicodedata.normalize("NFKD", low) if not unicodedata.combining(c)
    )
    low = _GLUED_UNIT_RE.sub(" ", low)
    return _NONWORD_RE.sub(" ", low).strip()


def _singularize(word: str) -> str:
    """Crude plural folding, applied to both sides so they always agree.

    Same rules as ``food_resolve.singularize``; correctness of the *English* is
    irrelevant as long as a rule phrase and a query fold identically.
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("oes", "ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokenize(raw: str) -> tuple[str, ...]:
    """Normalized, singular-folded tokens."""
    return tuple(_singularize(w) for w in normalize_text(raw).split() if w)


@dataclass(frozen=True)
class PhraseMatch:
    phrase: str  # the rule phrase as written in the rule data
    start: int
    length: int
    value: object


def build_phrase_index(
    phrases: Mapping[str, object],
) -> tuple[dict[tuple[str, ...], tuple[str, object]], int]:
    """``{folded token tuple: (original phrase, value)}`` plus the longest key.

    A later phrase folding onto an earlier one keeps the first — rule data is
    ordered, so this is deterministic rather than whichever-dict-order.
    """
    index: dict[tuple[str, ...], tuple[str, object]] = {}
    longest = 1
    for phrase, value in phrases.items():
        key = tokenize(phrase)
        if not key:
            continue
        index.setdefault(key, (phrase, value))
        longest = max(longest, len(key))
    return index, longest


def match_phrases(
    tokens: Sequence[str],
    index: Mapping[tuple[str, ...], tuple[str, object]],
    max_words: int,
) -> tuple[tuple[PhraseMatch, ...], tuple[str, ...]]:
    """Longest-match-first, left-to-right, **consuming** phrase matching.

    The consuming part is the material boundary (ADR-0001): once ``almond
    milk`` matches, its tokens are gone and ``milk`` cannot match them.
    """
    matches: list[PhraseMatch] = []
    leftover: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        for width in range(min(max_words, n - i), 0, -1):
            key = tuple(tokens[i:i + width])
            found = index.get(key)
            if found is not None:
                matches.append(PhraseMatch(found[0], i, width, found[1]))
                i += width
                break
        else:
            leftover.append(tokens[i])
            i += 1
    return tuple(matches), tuple(leftover)


_INDEX_CACHE: dict[str, tuple[dict[tuple[str, ...], tuple[str, object]], int]] = {}


def _index_for(ruleset: AllergenRuleset):
    cached = _INDEX_CACHE.get(ruleset.version)
    if cached is None:
        cached = build_phrase_index(ruleset.phrases)
        _INDEX_CACHE[ruleset.version] = cached
    return cached


# --- allergen key resolution -------------------------------------------------

# What users and labels actually write, mapped to ruleset keys. Unknown names
# are rejected rather than ignored: a profile asking to avoid "tree nut" and
# silently getting nothing screened is the failure mode of a lenient parser.
_KEY_ALIASES: Mapping[str, str] = {
    "dairy": "milk", "lactose": "milk", "cows milk": "milk", "cow milk": "milk",
    "eggs": "egg", "hen egg": "egg",
    "tree nut": "tree_nuts", "tree nuts": "tree_nuts", "nuts": "tree_nuts",
    "nut": "tree_nuts",
    "peanut": "peanuts", "groundnut": "peanuts", "groundnuts": "peanuts",
    "soy": "soybeans", "soya": "soybeans", "soybean": "soybeans",
    "shellfish": "crustacean_shellfish", "crustacean": "crustacean_shellfish",
    "crustaceans": "crustacean_shellfish", "crustacean shellfish":
        "crustacean_shellfish",
    "mollusc": "molluscs", "mollusk": "molluscs", "mollusks": "molluscs",
    "sulphites": "sulfites", "sulphite": "sulfites", "sulfite": "sulfites",
    "gluten free": "gluten",
    "fishes": "fish", "seafood": "fish",
    "sesame seed": "sesame", "sesame seeds": "sesame",
}


def resolve_allergen_key(name: str, ruleset: AllergenRuleset | None = None) -> str:
    """A user- or label-supplied allergen name as a ruleset key. Never guesses."""
    rs = ruleset or allergen_ruleset()
    raw = (name or "").strip().lower()
    if raw.startswith(CUSTOM_PREFIX) and raw in rs.labels:
        return raw
    squashed = re.sub(r"[\s_-]+", " ", raw).strip()
    underscored = squashed.replace(" ", "_")
    for candidate in (underscored, squashed, _KEY_ALIASES.get(squashed, "")):
        if candidate and candidate in rs.labels:
            return candidate
    mapped = _KEY_ALIASES.get(squashed)
    if mapped:
        return mapped
    raise ValueError(
        f"unknown allergen {name!r}; known keys: {sorted(rs.labels)}"
    )


US_MAJOR_ALLERGENS: tuple[str, ...] = (
    "milk", "egg", "fish", "crustacean_shellfish", "tree_nuts",
    "peanuts", "wheat", "soybeans", "sesame",
)


# --- inputs ------------------------------------------------------------------

@dataclass(frozen=True)
class IngredientRef:
    """One ingredient line, as plain data.

    ``declared_contains`` / ``declared_may_contain`` are label declarations and
    outrank anything inferred from the text. ``components`` is a declared
    composition for a compound ingredient (a sauce whose recipe we hold): when
    present, the parent's own text no longer triggers an opacity ``unknown``,
    because the composition IS the answer.
    """

    text: str
    declared_contains: tuple[str, ...] = ()
    declared_may_contain: tuple[str, ...] = ()
    components: tuple["IngredientRef", ...] = ()
    optional: bool = False
    provenance: Provenance | None = None


@dataclass(frozen=True)
class SensitivityProfile:
    """What this person avoids, and how hard.

    The default policy is ``STRICT`` because the fail-closed direction is the
    only defensible default for a profile that exists to avoid an allergen.
    """

    allergens: tuple[str, ...] = US_MAJOR_ALLERGENS
    custom_exclusions: tuple[str, ...] = ()
    policy: AvoidancePolicy = AvoidancePolicy.STRICT

    @classmethod
    def us_major_allergens(
        cls, policy: AvoidancePolicy = AvoidancePolicy.STRICT
    ) -> "SensitivityProfile":
        return cls(allergens=US_MAJOR_ALLERGENS, policy=policy)


# --- outputs -----------------------------------------------------------------

@dataclass(frozen=True)
class Evidence:
    """Why a verdict says what it says. Always present, always specific."""

    ingredient: str
    matched_term: str
    rule_id: str
    rule_version: str
    match_kind: str
    contributes: AllergenState
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
class AllergenVerdict:
    """One allergen's state for one recipe, with its evidence.

    Has no truth value. The only boolean is :meth:`permitted_under`, and its
    ``policy`` argument has no default.
    """

    allergen: str
    label: str
    state: AllergenState
    evidence: tuple[Evidence, ...]
    ruleset_version: str

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError(
                f"verdict for {self.allergen!r} has no evidence; a "
                "classification without an evidence trail is a bug"
            )

    def __bool__(self) -> bool:
        raise TypeError(
            _NO_TRUTH_VALUE.format(cls="AllergenVerdict", method="permitted_under")
        )

    def permitted_under(self, policy: AvoidancePolicy) -> bool:
        """Whether this state is acceptable under ``policy``.

        ``policy`` is required. Under ``STRICT`` this is False for
        ``CONTAINS``, ``MAY_CONTAIN`` and ``UNKNOWN``.
        """
        if not isinstance(policy, AvoidancePolicy):
            raise TypeError(
                "policy must be an AvoidancePolicy, not "
                f"{type(policy).__name__} — there is no default on purpose"
            )
        return self.state not in _EXCLUDED_UNDER[policy]

    @property
    def needs_review(self) -> bool:
        """True when the state is anything other than positive or negative."""
        return self.state in (AllergenState.MAY_CONTAIN, AllergenState.UNKNOWN)

    def explain(self) -> str:
        lines = [f"{self.label}: {self.state}"]
        lines += [f"  - {e.describe()}" for e in self.evidence]
        return "\n".join(lines)

    def __str__(self) -> str:  # pragma: no cover - human-facing only
        return f"{self.label}: {self.state}"


@dataclass(frozen=True)
class IngredientCoverage:
    """What the engine understood about one ingredient line."""

    ingredient: str
    recognised: tuple[str, ...]
    unscreened: tuple[str, ...]  # reasons; empty means fully screened

    @property
    def fully_screened(self) -> bool:
        return not self.unscreened


@dataclass(frozen=True)
class ScreeningDecision:
    """A boolean you can only obtain by naming a policy."""

    policy: AvoidancePolicy
    allowed: bool
    blocking: tuple[AllergenVerdict, ...]
    flagged: tuple[AllergenVerdict, ...]
    ruleset_version: str

    def __post_init__(self) -> None:
        if self.allowed != (not self.blocking):
            raise ValueError("decision.allowed disagrees with decision.blocking")

    def __bool__(self) -> bool:
        return self.allowed

    def reasons(self) -> tuple[str, ...]:
        return tuple(
            f"{v.label}: {v.state}" + (f" — {v.evidence[0].describe()}" if v.evidence else "")
            for v in self.blocking
        )


@dataclass(frozen=True)
class AllergenScreening:
    """Every requested allergen's verdict for one recipe.

    Has no truth value; call :meth:`decide` with a policy.
    """

    verdicts: tuple[AllergenVerdict, ...]
    coverage: tuple[IngredientCoverage, ...]
    ruleset_version: str
    profile: SensitivityProfile

    def __bool__(self) -> bool:
        raise TypeError(_NO_TRUTH_VALUE.format(cls="AllergenScreening", method="decide"))

    def verdict(self, allergen: str) -> AllergenVerdict:
        key = resolve_allergen_key(allergen) if CUSTOM_PREFIX not in allergen else allergen
        for v in self.verdicts:
            if v.allergen in (allergen, key):
                return v
        raise KeyError(f"{allergen!r} was not screened; screened: "
                       f"{[v.allergen for v in self.verdicts]}")

    def decide(self, policy: AvoidancePolicy) -> ScreeningDecision:
        """Apply ``policy`` to every verdict. ``policy`` is required."""
        if not isinstance(policy, AvoidancePolicy):
            raise TypeError(
                "policy must be an AvoidancePolicy, not "
                f"{type(policy).__name__} — there is no default on purpose"
            )
        blocking = tuple(v for v in self.verdicts if not v.permitted_under(policy))
        flagged = tuple(
            v for v in self.verdicts if v.needs_review and v not in blocking
        )
        return ScreeningDecision(
            policy=policy,
            allowed=not blocking,
            blocking=blocking,
            flagged=flagged,
            ruleset_version=self.ruleset_version,
        )

    @property
    def fully_screened(self) -> bool:
        return all(c.fully_screened for c in self.coverage)

    def explain(self) -> str:
        return "\n".join(v.explain() for v in self.verdicts)


# --- the engine --------------------------------------------------------------

@dataclass
class _Scan:
    label: str
    matched: dict[str, list[Evidence]] = field(default_factory=dict)
    recognised: list[str] = field(default_factory=list)
    unscreened: list[str] = field(default_factory=list)

    def add(self, allergen: str, ev: Evidence) -> None:
        self.matched.setdefault(allergen, []).append(ev)


def _provenance(ruleset: AllergenRuleset, rule_id: str, kind: str) -> Provenance:
    return Provenance(
        source=ruleset.source,
        source_id=rule_id,
        retrieved=ruleset.retrieved,
        method="declared" if kind in _DECLARED_KINDS else "inferred",
        version=ruleset.version,
        confidence=_CONFIDENCE.get(kind, 0.5),
        license=ruleset.license,
    )


def _evidence(
    ruleset: AllergenRuleset,
    *,
    ingredient: str,
    matched_term: str,
    allergen: str,
    kind: str,
    state: AllergenState,
    note: str = "",
) -> Evidence:
    rule_id = f"{allergen}.{kind}" + (f".{matched_term}" if matched_term else "")
    return Evidence(
        ingredient=ingredient,
        matched_term=matched_term,
        rule_id=rule_id,
        rule_version=ruleset.version,
        match_kind=kind,
        contributes=state,
        note=note,
        provenance=_provenance(ruleset, rule_id, kind),
    )


def _split_precaution(text: str, markers: Sequence[str]) -> tuple[str, str]:
    """``(main, precautionary)`` — everything from the first marker onwards is
    a precautionary statement and can only ever yield ``may_contain``."""
    normalized = normalize_text(text)
    cut = None
    for marker in markers:
        found = normalized.find(normalize_text(marker))
        if found >= 0 and (cut is None or found < cut):
            cut = found
    if cut is None:
        return normalized, ""
    return normalized[:cut].strip(), normalized[cut:].strip()


def _free_from_claims(
    tokens: Sequence[str], ruleset: AllergenRuleset
) -> dict[str, str]:
    """Allergen keys the ingredient's own text claims to be free of.

    A claim NEVER clears an allergen; it downgrades an inferred ``contains`` to
    ``may_contain``, because we have read a claim, not verified a label.
    """
    claims: dict[str, str] = {}
    folded = {k: tokenize(k) for k in ruleset.free_from}
    for i, token in enumerate(tokens[:-1]):
        if tokens[i + 1] != "free":
            continue
        for claim, key_tokens in folded.items():
            if key_tokens and key_tokens[-1] == token:
                for allergen in ruleset.free_from[claim]:
                    claims.setdefault(allergen, f"declared {claim}-free")
    if "vegan" in tokens:
        for allergen in ruleset.vegan_claim_allergens:
            claims.setdefault(allergen, "declared vegan")
    return claims


def _scan_ingredient(
    ing: IngredientRef,
    ruleset: AllergenRuleset,
    path: str = "",
) -> list[_Scan]:
    """One ``_Scan`` per screened line (a compound yields its components too)."""
    label = f"{path} → {ing.text}" if path else ing.text
    scan = _Scan(label=label)
    index, max_words = _index_for(ruleset)

    def record(allergen: str, term: str, kind: str, state: AllergenState, note: str = "") -> None:
        scan.add(allergen, _evidence(
            ruleset, ingredient=label, matched_term=term, allergen=allergen,
            kind=kind, state=state, note=note,
        ))

    main, precaution = _split_precaution(ing.text, ruleset.precaution_markers)
    main_tokens = tuple(_singularize(w) for w in main.split() if w)
    precaution_tokens = tuple(_singularize(w) for w in precaution.split() if w)

    claims = _free_from_claims(main_tokens, ruleset)

    matches, leftover = match_phrases(main_tokens, index, max_words)
    opaque_hit = ""
    for match in matches:
        scan.recognised.append(match.phrase)
        if match.phrase in ruleset.opaque and not ing.components:
            opaque_hit = opaque_hit or match.phrase
        for hit in match.value:  # type: ignore[attr-defined]
            assert isinstance(hit, PhraseHit)
            state = (
                AllergenState.CONTAINS if hit.state == "contains"
                else AllergenState.MAY_CONTAIN
            )
            note = hit.note
            kind = hit.kind
            claim = claims.get(hit.allergen)
            if claim and state is AllergenState.CONTAINS:
                state = AllergenState.MAY_CONTAIN
                kind = "declared_free_from"
                note = f"{claim}; claim not verified" + (f" — {note}" if note else "")
            record(hit.allergen, match.phrase, kind, state, note)

    # Precautionary segment: matches here can only be `may_contain`.
    if precaution_tokens:
        prec_matches, _ = match_phrases(precaution_tokens, index, max_words)
        for match in prec_matches:
            scan.recognised.append(match.phrase)
            for hit in match.value:  # type: ignore[attr-defined]
                record(
                    hit.allergen, match.phrase, "precautionary_statement",
                    AllergenState.MAY_CONTAIN,
                    "stated as a precautionary warning on the ingredient",
                )

    # Label declarations outrank everything inferred, and are never downgraded.
    for name in ing.declared_contains:
        key = resolve_allergen_key(name, ruleset)
        record(key, name, "declared_label", AllergenState.CONTAINS,
               "declared on the ingredient's own label")
    for name in ing.declared_may_contain:
        key = resolve_allergen_key(name, ruleset)
        record(key, name, "may_contain_label", AllergenState.MAY_CONTAIN,
               "precautionary declaration on the ingredient's own label")

    # What we could not screen. Both halves matter: an opaque composite whose
    # head word we recognise ("secret sauce") is no more screened than text we
    # did not recognise at all.
    unrecognised = tuple(
        t for t in leftover
        if t not in ruleset.stop_words and not _NUMERIC_RE.match(t)
    )
    if opaque_hit:
        scan.unscreened.append(
            f"{opaque_hit!r} is a composite whose ingredients are not declared"
        )
    if unrecognised and not ing.components:
        scan.unscreened.append(
            "unrecognised: " + ", ".join(sorted(set(unrecognised)))
        )

    # An optional ingredient can be left out, so positive evidence becomes
    # precautionary — but never a clearance, so `unknown` stays `unknown`.
    if ing.optional:
        for allergen, evidence in scan.matched.items():
            downgraded = []
            for ev in evidence:
                if ev.contributes is AllergenState.CONTAINS:
                    downgraded.append(_evidence(
                        ruleset, ingredient=label, matched_term=ev.matched_term,
                        allergen=allergen, kind="optional_ingredient",
                        state=AllergenState.MAY_CONTAIN,
                        note=f"ingredient is optional; would otherwise be "
                             f"'contains' via {ev.match_kind}",
                    ))
                else:
                    downgraded.append(ev)
            scan.matched[allergen] = downgraded

    scans = [scan]
    for component in ing.components:
        scans.extend(_scan_ingredient(component, ruleset, path=label))
    return scans


def _contributing(ruleset: AllergenRuleset, key: str) -> tuple[str, ...]:
    """``key`` plus every allergen that implies it (wheat implies gluten)."""
    extra = tuple(k for k, implied in ruleset.implies.items() if key in implied)
    return (key, *extra)


def screen(
    ingredients: Iterable[IngredientRef | str],
    profile: SensitivityProfile | None = None,
    *,
    ruleset_version: str | None = None,
) -> AllergenScreening:
    """Screen ``ingredients`` against ``profile``.

    Returns an :class:`AllergenScreening`, which has no truth value. To get a
    boolean, call :meth:`AllergenScreening.decide` with an explicit
    :class:`AvoidancePolicy`.
    """
    prof = profile or SensitivityProfile.us_major_allergens()
    ruleset = allergen_ruleset(ruleset_version)
    if prof.custom_exclusions:
        ruleset = ruleset.with_custom_exclusions(prof.custom_exclusions)

    refs = [
        IngredientRef(text=i) if isinstance(i, str) else i for i in ingredients
    ]
    scans: list[_Scan] = []
    for ref in refs:
        scans.extend(_scan_ingredient(ref, ruleset))

    keys: list[str] = []
    for name in prof.allergens:
        key = resolve_allergen_key(name, ruleset)
        if key not in keys:
            keys.append(key)
    for term in prof.custom_exclusions:
        key = CUSTOM_PREFIX + term.strip().lower()
        if key in ruleset.labels and key not in keys:
            keys.append(key)

    verdicts = []
    for key in keys:
        contributing = _contributing(ruleset, key)
        evidence: list[Evidence] = []
        for scan in scans:
            found = [ev for a in contributing for ev in scan.matched.get(a, [])]
            evidence.extend(found)
            if scan.unscreened and not found:
                # This line could not be screened and produced nothing for this
                # allergen — which is `unknown`, not "nothing found".
                for reason in scan.unscreened:
                    evidence.append(_evidence(
                        ruleset, ingredient=scan.label, matched_term="",
                        allergen=key,
                        kind=("opaque_composite" if "composite" in reason
                              else "unrecognised_text"),
                        state=AllergenState.UNKNOWN, note=reason,
                    ))
        if evidence:
            state = max(
                (ev.contributes for ev in evidence), key=lambda s: _PRECEDENCE[s]
            )
        else:
            state = AllergenState.NO_KNOWN_INGREDIENT_IDENTIFIED
            evidence = [_evidence(
                ruleset, ingredient="", matched_term="", allergen=key,
                kind="coverage", state=state,
                note=(
                    f"{len(scans)} ingredient line(s) screened against "
                    f"{ruleset.version}; every line was recognised and none "
                    f"matched a {ruleset.label(key)} rule. This is not a "
                    "safety claim — it is bounded by the rule data."
                ),
            )]
        verdicts.append(AllergenVerdict(
            allergen=key,
            label=ruleset.label(key),
            state=state,
            evidence=tuple(evidence),
            ruleset_version=ruleset.version,
        ))

    coverage = tuple(
        IngredientCoverage(
            ingredient=s.label,
            recognised=tuple(s.recognised),
            unscreened=tuple(s.unscreened),
        )
        for s in scans
    )
    return AllergenScreening(
        verdicts=tuple(verdicts),
        coverage=coverage,
        ruleset_version=ruleset.version,
        profile=prof,
    )


def screen_text(
    text: str,
    profile: SensitivityProfile | None = None,
    *,
    ruleset_version: str | None = None,
) -> AllergenScreening:
    """Screen a single ingredient line. Convenience over :func:`screen`."""
    return screen([IngredientRef(text=text)], profile,
                  ruleset_version=ruleset_version)
