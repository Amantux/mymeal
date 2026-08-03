"""The SLM structuring pass over ingredient lines the parser couldn't read.

The deterministic parser is the product; this pass is an improvement on top. So
the tests that matter most are the ones proving it can never make things worse:
it must not be consulted about lines that already parsed, and every failure mode
must fall back to the deterministic result rather than break an import.
"""
import pytest

from app.services import ingredient_ai


class Stub:
    """A provider that returns a canned payload and counts calls."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete_json(self, prompt, system="", max_tokens=4096):
        self.calls += 1
        self.last_prompt = prompt
        return self.payload


class Exploding:
    def complete_json(self, *a, **k):
        raise RuntimeError("model unavailable")


PROSE = "a good handful of parsley"
CLEAN = "1 1/2 cups all-purpose flour"


def test_a_line_the_parser_read_is_never_sent_to_the_model():
    """The model must not be able to overwrite a deterministic answer, and a
    tidy recipe should cost nothing."""
    provider = Stub({"items": []})

    ingredient_ai.structure([CLEAN, "250g plain flour"], provider=provider)

    assert provider.calls == 0


def test_only_the_unreadable_lines_are_sent():
    provider = Stub({"items": []})

    ingredient_ai.structure([CLEAN, PROSE], provider=provider)

    assert PROSE in provider.last_prompt
    assert CLEAN not in provider.last_prompt


def test_a_usable_proposal_is_returned_against_its_original_line():
    provider = Stub({"items": [
        {"index": 0, "quantity": 1, "unit": "handful", "food": "parsley",
         "note": "roughly chopped", "confidence": 0.8},
    ]})

    out = ingredient_ai.structure([PROSE], provider=provider)

    assert out[PROSE]["quantity"] == 1.0
    assert out[PROSE]["unit"] == "handful"
    assert out[PROSE]["food"] == "parsley"
    assert out[PROSE]["display"] == PROSE       # the human's line, untouched
    assert out[PROSE]["source"] == "ai"


def test_no_provider_configured_is_a_normal_outcome():
    """The commonest deployment has no AI at all; it must import unchanged."""
    assert ingredient_ai.structure([PROSE], provider=None) == {}


# --- fail-open: every one of these must keep the deterministic result --------

@pytest.mark.parametrize("payload", [
    "not a dict",
    {"items": "not a list"},
    {},
    {"items": [None, 42, "text"]},
])
def test_a_malformed_response_yields_nothing_rather_than_raising(payload):
    assert ingredient_ai.structure([PROSE], provider=Stub(payload)) == {}


def test_a_provider_that_raises_yields_nothing():
    assert ingredient_ai.structure([PROSE], provider=Exploding()) == {}


@pytest.mark.parametrize("bad", [
    {"index": 0, "quantity": -5, "food": "parsley", "confidence": 1},
    {"index": 0, "quantity": 99999, "food": "parsley", "confidence": 1},
    {"index": 0, "quantity": "lots", "food": "parsley", "confidence": 1},
    {"index": 0, "quantity": 1, "food": "", "confidence": 1},
])
def test_an_implausible_proposal_is_discarded(bad):
    """A confidently wrong quantity changes what someone cooks."""
    assert ingredient_ai.structure([PROSE], provider=Stub({"items": [bad]})) == {}


def test_a_proposal_for_a_line_we_never_sent_is_discarded():
    """The model must not be able to attach a proposal to an arbitrary line."""
    out = ingredient_ai.structure(
        [PROSE], provider=Stub({"items": [
            {"index": 99, "quantity": 1, "food": "ghost", "confidence": 1}]}))

    assert out == {}


def test_an_unknown_unit_is_dropped_but_the_rest_is_kept():
    """A unit the app cannot scale or convert looks structured while behaving
    like free text, which is worse than no unit at all."""
    out = ingredient_ai.structure([PROSE], provider=Stub({"items": [
        {"index": 0, "quantity": 2, "unit": "smidgen", "food": "parsley",
         "confidence": 0.9}]}))

    assert out[PROSE]["unit"] is None
    assert out[PROSE]["quantity"] == 2.0


def test_confidence_is_clamped():
    out = ingredient_ai.structure([PROSE], provider=Stub({"items": [
        {"index": 0, "quantity": 1, "food": "parsley", "confidence": 7}]}))

    assert out[PROSE]["confidence"] == 1.0


def test_the_number_of_lines_sent_is_bounded():
    """A bad scrape must not turn into an enormous prompt to a small model."""
    provider = Stub({"items": []})

    # Lines that genuinely defeat the parser — a leading digit would make them
    # parse and never reach the model, which is what the first draft of this
    # test accidentally asserted.
    ingredient_ai.structure([f"a good handful of herb number {i}" for i in range(200)],
                            provider=provider)

    assert provider.calls == 1
    assert provider.last_prompt.count("\n") < ingredient_ai.MAX_LINES + 20
