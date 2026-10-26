"""The held-out corpora, run as tests.

Design note on what is ASSERTED versus merely REPORTED, because getting this
wrong makes the suite useless in one of two opposite ways.

Asserted (hard failures) -- invariants that must never regress:
  * material boundaries: "almond milk" is not milk (ADR-0001, allergen blast
    radius)
  * declines: an unanswerable or ambiguous request returns None, never a guess
  * equivalence: a functional qualifier does not lose the weight
  * discrimination: near neighbours do not collapse into each other
  * every RESOLVED value the engine DOES answer must be right

Reported, not asserted -- coverage, which is meant to climb over several
changes:
  * how many catalog terms are known
  * how many RESOLVED cases resolve at all

A suite that fails until coverage is perfect gets ignored or deleted, and then
the invariants above stop being guarded too. So coverage lives in a regression
gate against tools/corpus_baseline.json (ratchet: it may rise, never fall)
while correctness is asserted outright.

The distinction matters for the narrower reason as well: an engine that
declines to answer is doing the right thing, and a test that cannot tell
"declined" from "answered wrongly" would push someone to make it guess.
"""
from __future__ import annotations

import json
import os

import pytest

from fixtures import naming_corpus as nc
from fixtures import volume_weight_corpus as vw

from app.services import food_reference as ref
from app.services import food_resolve as fr

BASELINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "corpus_baseline.json")
TOL = 0.12


def _fold(s: str) -> str:
    return getattr(fr, "fold", lambda x: x.strip().lower())(s)


# ---------------------------------------------------------------- boundaries
@pytest.mark.parametrize("raw,want,reasons,note", nc.PRESERVE,
                         ids=[c[0] for c in nc.PRESERVE])
def test_material_and_functional_boundaries_are_never_crossed(raw, want, reasons, note):
    got, _qualifier, why = fr.normalize(raw)

    assert _fold(got) == _fold(want), f"{raw!r} -> {got!r}, must stay {want!r}: {note}"
    assert why in reasons, (
        f"{raw!r} gave the right answer for the wrong reason ({why!r} not in "
        f"{sorted(reasons)}). That is a latent bug: it will regress silently "
        f"when the catalog grows. {note}")


# ------------------------------------------------------------------ declines
@pytest.mark.parametrize("food,unit,note", vw.UNRESOLVABLE_FROM_DATASET,
                         ids=[c[0] for c in vw.UNRESOLVABLE_FROM_DATASET])
def test_an_unanswerable_conversion_declines_rather_than_guessing(food, unit, note):
    assert ref.grams_per(unit, food) is None, (
        f"{unit} of {food} is not answerable from this dataset ({note}); "
        f"a number here is invented")


@pytest.mark.parametrize("food,unit,spread,note", vw.AMBIGUOUS,
                         ids=[c[0] for c in vw.AMBIGUOUS])
def test_a_genuinely_ambiguous_food_declines_rather_than_picking_a_form(
        food, unit, spread, note):
    got = ref.grams_per(unit, food)
    assert got is None, (
        f"{unit} of bare {food!r} spans {min(spread)}-{max(spread)}g across forms "
        f"({note}); returning {got}g picks one silently")


# --------------------------------------------------------------- equivalence
@pytest.mark.parametrize("variant,base,note", nc.WEIGHT_EQUIVALENT,
                         ids=[f"{c[0]}~{c[1]}" for c in nc.WEIGHT_EQUIVALENT])
def test_a_functional_qualifier_does_not_change_the_weight(variant, base, note):
    gv, gb = ref.grams_per("cup", variant), ref.grams_per("cup", base)
    # Both-None must NOT pass: that is the shape of assertion that survives
    # deleting the feature entirely.
    if gv is None and gb is None:
        pytest.xfail(f"neither {variant!r} nor {base!r} converts yet -- coverage, "
                     f"not a broken invariant")
    assert gv is not None and gb is not None, f"{variant}={gv}, {base}={gb}"
    assert abs(gv - gb) / gb <= 0.02, f"{variant} {gv}g vs {base} {gb}g: {note}"


# ------------------------------------------------------------ discrimination
@pytest.mark.parametrize("variant,base,unit,gap,note", vw.DISTINCT_PAIRS,
                         ids=[f"{c[0]}!={c[1]}" for c in vw.DISTINCT_PAIRS])
def test_near_neighbours_do_not_collapse_into_one_weight(variant, base, unit, gap, note):
    gv, gb = ref.grams_per(unit, variant), ref.grams_per(unit, base)
    if gv is None:
        pytest.xfail(f"{variant!r} does not convert yet -- coverage. Declining is "
                     f"honest; it just is not evidence of discrimination")
    assert gb is not None, f"base {base!r} must convert for this case to mean anything"
    seen = abs(gv - gb) / gb
    assert seen >= gap * 0.6, (
        f"{variant} {gv}g and {base} {gb}g differ by {seen:.0%}, USDA publishes "
        f"~{gap:.0%}: {note}")


# ------------------------------------------------------------ answered-value
@pytest.mark.parametrize("food,unit,grams,fdc,note", vw.RESOLVED,
                         ids=[c[0] for c in vw.RESOLVED])
def test_an_answered_conversion_is_the_cross_validated_weight(food, unit, grams, fdc, note):
    got = ref.grams_per(unit, food)
    if got is None:
        pytest.xfail(f"{food!r} has no conversion yet -- coverage, tracked by the "
                     f"ratchet below, not a wrong answer")
    assert abs(got - grams) / grams <= TOL, (
        f"{unit} of {food}: got {got}g, USDA fdc {fdc} and King Arthur both say "
        f"~{grams}g. {note}")


# -------------------------------------------------------------- the ratchet
def _coverage() -> dict[str, int]:
    """Must measure the SAME seams as tools/validate_corpora.py.

    These drifted once: the validator moved `conversion` to the end-to-end
    path while this still read the reference stage, so the ratchet compared
    one seam's count against the other's baseline and failed on a change that
    improved both. Two measurements of one metric is the same mistake as two
    declarations of one version.
    """
    from app.services import units as u

    return {
        "catalog": sum(bool(fr.is_known(t)) for t in nc.MUST_BE_KNOWN),
        # End to end -- what a cook gets, matching the headline number.
        "conversion": sum(u.to_grams(f"1 {unit} {food}") is not None
                          for food, unit, _g, _i, _n in vw.RESOLVED),
        # The dataset stage alone, which is what carries provenance.
        "reference": sum(ref.grams_per(unit, food) is not None
                         for food, unit, _g, _i, _n in vw.RESOLVED),
        "normalisation": sum(
            _fold(fr.normalize(raw)[0]) == _fold(want) for raw, want, _r, _n in nc.STRIP),
    }


@pytest.mark.skipif(not os.path.exists(BASELINE), reason="no baseline recorded yet")
def test_coverage_never_falls_below_the_recorded_baseline():
    """A ratchet, not a target.

    Coverage is allowed to climb and forbidden to fall. Asserting an absolute
    number here would either be red for weeks (and get deleted) or need
    editing on every improvement, and an expectation that gets edited to match
    reality every week is not an expectation.
    """
    base = json.load(open(BASELINE))
    now = _coverage()
    regressions = {
        k: (base[k]["passed"], v)
        for k, v in now.items()
        if k in base and v < base[k]["passed"]
    }
    assert not regressions, "coverage fell: " + ", ".join(
        f"{k} {was}->{is_}" for k, (was, is_) in regressions.items())
