"""The provenance contract is the thing three other modules code against, so
its ENFORCEMENT is tested, not just its happy path. Each rejection test would
pass vacuously if the guard were removed, so they assert the raise explicitly.
"""
import dataclasses

import pytest

from foodcore.provenance import (
    LICENSES,
    METHODS,
    SOURCES,
    Provenance,
    Resolved,
    Unresolved,
    VocabularyError,
)


def prov(**kw) -> Provenance:
    base = dict(
        source="usda",
        source_id="168893",
        retrieved="2025-04-01",
        method="measured",
        version="convert/1",
        confidence=1.0,
        license="CC0-1.0",
    )
    base.update(kw)
    return Provenance(**base)


# --- vocabularies -------------------------------------------------------

def test_every_declared_method_is_accepted():
    for method in METHODS:
        assert prov(method=method).method == method


def test_every_declared_license_is_accepted():
    for lic in LICENSES:
        assert prov(license=lic).license == lic


def test_every_declared_source_is_accepted():
    for source in SOURCES:
        assert prov(source=source).source == source


@pytest.mark.parametrize("bad", ["mesured", "MEASURED", "guessed", "", None])
def test_unknown_method_is_rejected(bad):
    # A typo'd method would otherwise be stored and later be indistinguishable
    # from real evidence, so construction must fail loudly.
    with pytest.raises(VocabularyError):
        prov(method=bad)


@pytest.mark.parametrize("bad", ["CC0", "MIT", "proprietary", "", None])
def test_unknown_license_is_rejected(bad):
    with pytest.raises(VocabularyError):
        prov(license=bad)


@pytest.mark.parametrize("bad", ["USDA", "fdc", "openfoodfacts", "", None])
def test_unknown_source_is_rejected(bad):
    with pytest.raises(VocabularyError):
        prov(source=bad)


def test_vocabulary_error_names_the_accepted_values():
    with pytest.raises(VocabularyError) as exc:
        prov(method="guessed")
    message = str(exc.value)
    assert "guessed" in message
    for method in METHODS:
        assert method in message


# --- other field rules --------------------------------------------------

@pytest.mark.parametrize("bad", [-0.1, 1.1, 2, "high", None, True])
def test_confidence_outside_0_1_is_rejected(bad):
    with pytest.raises(ValueError):
        prov(confidence=bad)


@pytest.mark.parametrize("ok", [0.0, 0.5, 1, 1.0])
def test_confidence_bounds_are_inclusive(ok):
    assert prov(confidence=ok).confidence == ok


def test_version_is_required():
    # Without a version a stored value cannot be traced to the rule that made
    # it, which defeats the point of recording provenance at all.
    with pytest.raises(ValueError):
        prov(version="  ")


def test_source_id_may_be_empty_for_internal_defaults():
    assert prov(source="internal", source_id="", license="internal").source_id == ""


def test_provenance_is_frozen():
    p = prov()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.confidence = 0.1


# --- Resolved / Unresolved ---------------------------------------------

def test_resolved_round_trips_to_dict():
    r = Resolved(value=240.0, unit="g", provenance=prov(method="calculated"))
    assert r.to_dict() == {
        "status": "resolved",
        "value": 240.0,
        "unit": "g",
        "provenance": {
            "source": "usda",
            "sourceId": "168893",
            "retrieved": "2025-04-01",
            "method": "calculated",
            "version": "convert/1",
            "confidence": 1.0,
            "license": "CC0-1.0",
        },
    }


def test_resolved_requires_provenance():
    with pytest.raises(ValueError):
        Resolved(value=1.0, unit="g", provenance=None)


def test_resolved_requires_a_unit():
    with pytest.raises(ValueError):
        Resolved(value=1.0, unit="", provenance=prov())


def test_unresolved_round_trips_to_dict():
    u = Unresolved(
        reason="no_portion_data",
        detail="USDA has no portion weight for 'canned tomatoes'; give a weight or a volume.",
    )
    assert u.to_dict() == {
        "status": "unresolved",
        "reason": "no_portion_data",
        "detail": (
            "USDA has no portion weight for 'canned tomatoes'; give a weight or a volume."
        ),
    }


def test_unresolved_requires_a_reason_code_and_a_detail():
    with pytest.raises(ValueError):
        Unresolved(reason="", detail="something")
    with pytest.raises(ValueError):
        Unresolved(reason="no_portion_data", detail="")


def test_status_discriminates_without_inspecting_keys():
    resolved = Resolved(value=0.0, unit="g", provenance=prov())
    unresolved = Unresolved(reason="unknown_food", detail="No matching food.")
    # The whole point: a genuine ZERO is resolved, and "we don't know" is not
    # spelled with a zero. A consumer must never have to tell them apart by
    # looking at the number.
    assert resolved.to_dict()["status"] == "resolved"
    assert resolved.resolved is True
    assert unresolved.to_dict()["status"] == "unresolved"
    assert unresolved.resolved is False


# --- licences compose; the vocabulary stays closed -------------------------

def test_licences_compose_because_obligations_do():
    """A value derived from CC0 and ODbL inputs is subject to BOTH. Collapsing
    to one licence is how an ODbL share-alike duty silently goes missing, so an
    SPDX-style conjunction is a legitimate value."""
    p = Provenance(source="internal", source_id="usda:1+off:2",
                   retrieved="2026-01-01", method="calculated",
                   version="nutrition/1", confidence=0.9,
                   license="CC0-1.0 AND ODbL-1.0")

    assert p.license == "CC0-1.0 AND ODbL-1.0"


def test_a_conjunction_is_normalised_so_equal_combinations_compare_equal():
    a = Provenance(source="internal", source_id="", retrieved="2026-01-01",
                   method="calculated", version="v/1", confidence=1.0,
                   license="ODbL-1.0 AND CC0-1.0")

    assert a.license == "CC0-1.0 AND ODbL-1.0"   # sorted, deduped


def test_every_term_in_a_conjunction_must_still_be_known():
    """MUTATION GUARD. Accepting conjunctions must not become 'accept any
    string containing a known licence' — that would let an unvetted dataset in
    under cover of a CC0 term."""
    with pytest.raises(VocabularyError):
        Provenance(source="usda", source_id="", retrieved="2026-01-01",
                   method="measured", version="v/1", confidence=1.0,
                   license="CC0-1.0 AND Proprietary-EULA")

    with pytest.raises(VocabularyError):
        Provenance(source="usda", source_id="", retrieved="2026-01-01",
                   method="measured", version="v/1", confidence=1.0,
                   license="CC0-1.0,ODbL-1.0")      # comma is not the separator
