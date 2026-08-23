"""The AI pass that fills recipe-level gaps a deterministic read left.

The sibling of the ingredient structuring pass, and it lives or dies on the same
promise: it may fill a hole, but it may never invent. Asking a model for a METHOD
when the text has none is an open invitation to write a plausible one, and the
user would have no way to tell it wasn't in what they pasted — so every proposal
is checked against the source before it is accepted.
"""
import pytest

from app.services import recipe_complete
from app.services.recipe_complete import complete, missing_fields

SOURCE = """Grandma's Lemon Drizzle Cake
Serves 8

225g unsalted butter
4 large eggs
225g self-raising flour

Heat the oven to 180C.
Beat the butter and sugar until pale.
Bake for 45 minutes until golden.
"""


class FakeProvider:
    """Returns a canned object and records the prompt it was given."""

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete_json(self, prompt, system=""):
        self.prompts.append(prompt)
        return self.answer


def _payload(**over):
    base = {"name": "Imported Recipe", "steps": [], "servings": 0,
            "ingredients": [{"display": "225g unsalted butter"}]}
    base.update(over)
    return base


# --- Which gaps are worth a model call ---------------------------------------

@pytest.mark.parametrize("payload,expected", [
    (_payload(), ["name", "steps", "servings"]),
    (_payload(name="Real Title"), ["steps", "servings"]),
    (_payload(steps=[{"title": "", "text": "Mix."}]), ["name", "servings"]),
    (_payload(servings=4), ["name", "steps"]),
    (_payload(name="Real", steps=[{"title": "", "text": "Mix."}], servings=4), []),
    # The placeholder is a gap, not a title.
    (_payload(name="Imported Recipe"), ["name", "steps", "servings"]),
    (_payload(name="   "), ["name", "steps", "servings"]),
])
def test_missing_fields_reports_only_what_is_empty(payload, expected):
    assert missing_fields(payload) == expected


def test_a_complete_payload_never_reaches_the_model():
    """The fast path stays instant and free — a recipe that parsed cleanly does
    not pay for a model call."""
    provider = FakeProvider({"name": "Something Else"})

    got = complete(_payload(name="Real", steps=[{"title": "", "text": "Mix."}],
                            servings=4), SOURCE, provider=provider)

    assert provider.prompts == []
    assert got["name"] == "Real"


def test_only_the_missing_fields_are_asked_for():
    provider = FakeProvider({})

    complete(_payload(name="Real", servings=4), SOURCE, provider=provider)

    prompt = provider.prompts[0]
    assert '"steps"' in prompt
    assert '"name"' not in prompt and '"servings"' not in prompt


# --- Nothing already read is overwritten -------------------------------------

def test_a_field_that_was_read_is_never_replaced():
    read_steps = [{"title": "", "text": "Beat the butter and sugar until pale."}]
    provider = FakeProvider({"name": "Model's Title", "servings": 99,
                             "steps": [{"text": "Heat the oven to 180C."}]})

    got = complete(_payload(name="Deterministic Title", servings=4,
                            steps=read_steps), SOURCE, provider=provider)

    assert got["name"] == "Deterministic Title"
    assert got["servings"] == 4
    assert got["steps"] == read_steps      # even a grounded proposal cannot win


def test_the_ingredients_are_never_touched():
    """They came from the user's own text, exactly. The model is not asked for
    them and could not change them if it answered with some."""
    provider = FakeProvider({"ingredients": [{"display": "1 kg gold"}],
                             "servings": 8})

    got = complete(_payload(), SOURCE, provider=provider)

    assert [i["display"] for i in got["ingredients"]] == ["225g unsalted butter"]


# --- Anti-invention: the grounding check -------------------------------------

def test_a_method_that_is_in_the_text_is_accepted():
    provider = FakeProvider({"steps": [
        {"text": "Heat the oven to 180C."},
        {"text": "Beat the butter and sugar until pale."},
    ]})

    got = complete(_payload(), SOURCE, provider=provider)

    assert [s["text"] for s in got["steps"]] == [
        "Heat the oven to 180C.", "Beat the butter and sugar until pale."]


def test_an_invented_method_is_rejected():
    """The whole point. A model asked for a method it cannot find will write a
    fluent one; those sentences are made of words that are not in the source."""
    provider = FakeProvider({"steps": [
        {"text": "Marinate the chicken thighs overnight in yoghurt and spices."},
        {"text": "Grill over charcoal, turning frequently, until charred."},
    ]})

    got = complete(_payload(), SOURCE, provider=provider)

    assert got["steps"] == []


# The dangerous class: fabrications built ENTIRELY from the source's own
# vocabulary. Recipe prose reuses a small set of words, so a bag-of-words check
# passed every one of these — including the inversion.

COOKING_SOURCE = """Chicken and Rice
Ingredients
2 chicken thighs
200g rice
Heat the oil in a pan and brown the garlic for 2 minutes.
Add the rice and stock, then simmer the chicken for 20 minutes until tender.
Rest for 5 minutes, then serve.
"""


@pytest.mark.parametrize("fabricated", [
    "Serve the chicken thighs raw in the hot stock.",
    "Do not cook the chicken; rest until softened.",
    "Pour the hot oil into the rice, then brown the garlic for 20 minutes.",
    "Rest the raw chicken in the pan for 5 minutes, then serve.",
])
def test_a_fabrication_built_from_the_sources_own_words_is_rejected(fabricated):
    got = complete(_payload(), COOKING_SOURCE,
                   provider=FakeProvider({"steps": [{"text": fabricated}]}))

    assert got["steps"] == []


@pytest.mark.parametrize("tampered", [
    "Rest for 50 minutes, then serve.",              # 5 -> 50
    "Heat the oil in a pan and brown the garlic for 9 minutes.",
])
def test_a_changed_time_or_temperature_is_rejected(tampered):
    """The most dangerous thing this module could do. Words alone cannot see
    digits, so "45 minutes" and "5 minutes" were indistinguishable — a silently
    halved cook time on chicken, or an oven moved from 180C to 240C."""
    got = complete(_payload(), COOKING_SOURCE,
                   provider=FakeProvider({"steps": [{"text": tampered}]}))

    assert got["steps"] == []


def test_the_real_method_is_still_accepted_verbatim():
    """The guard has to admit the truth as readily as it refuses invention."""
    real = ["Heat the oil in a pan and brown the garlic for 2 minutes.",
            "Add the rice and stock, then simmer the chicken for 20 minutes until tender."]
    got = complete(_payload(), COOKING_SOURCE,
                   provider=FakeProvider({"steps": [{"text": t} for t in real]}))

    assert [s["text"] for s in got["steps"]] == real


def test_a_step_that_kept_its_numbering_is_still_accepted():
    """A model that ignores "drop any 1. numbering" adds a digit that isn't in
    the source, which the numeric gate would otherwise reject."""
    got = complete(_payload(), COOKING_SOURCE, provider=FakeProvider(
        {"steps": [{"text": "1. Rest for 5 minutes, then serve."}]}))

    assert [s["text"] for s in got["steps"]] == ["Rest for 5 minutes, then serve."]


def test_the_grounding_threshold_is_load_bearing():
    """Pins the constant. Without this, every value from 0.3 to 1.0 kept the
    suite green and a refactor could accept almost any cooking sentence.

    Only the LOWER bound is pinned, deliberately. Measured: a step that merges
    two source lines, or drops a trailing clause, still scores 1.00 — shingles
    are taken over the whole source token stream, so a line boundary is not a
    barrier. Raising the threshold therefore makes the guard stricter without
    rejecting anything genuinely from the source, which
    test_the_real_method_is_still_accepted_verbatim already covers. 0.7 is a
    small deliberate margin, not a measured requirement.
    """
    from app.services import recipe_complete as rc

    # A real sentence must pass at the configured threshold...
    real = "Add the rice and stock, then simmer the chicken for 20 minutes until tender."
    kept = complete(_payload(), COOKING_SOURCE,
                    provider=FakeProvider({"steps": [{"text": real}]}))
    assert kept["steps"], "the configured threshold rejects a verbatim step"

    # ...and a fabrication must fail at it. Raising the bar to 1.0 must not be
    # needed for that, and lowering it to 0.3 must not be enough to let it in.
    fake = "Rest the raw chicken in the pan, then brown the garlic and serve."
    assert complete(_payload(), COOKING_SOURCE,
                    provider=FakeProvider({"steps": [{"text": fake}]}))["steps"] == []
    assert rc._GROUNDING > 0.5, "a threshold this low admits any cooking sentence"
    assert rc._SHINGLE >= 3, "shorter n-grams stop encoding word order"

    # An embellishment — one word the source never used — is invention, however
    # small, and must not slip through on the strength of the surrounding words.
    embellished = "Heat the extra-virgin oil in a pan and brown the garlic for 2 minutes."
    assert complete(_payload(), COOKING_SOURCE,
                    provider=FakeProvider({"steps": [{"text": embellished}]}))["steps"] == []


def test_a_padded_step_loses_only_itself():
    """A model that read three real steps and then added a fourth keeps the
    three: each step is checked on its own."""
    provider = FakeProvider({"steps": [
        {"text": "Heat the oven to 180C."},
        {"text": "Bake for 45 minutes until golden."},
        {"text": "Serve with a scoop of pistachio gelato and candied kumquats."},
    ]})

    got = complete(_payload(), SOURCE, provider=provider)

    assert [s["text"] for s in got["steps"]] == [
        "Heat the oven to 180C.", "Bake for 45 minutes until golden."]


def test_an_invented_title_is_rejected():
    provider = FakeProvider({"name": "Moroccan Spiced Lamb Tagine"})

    got = complete(_payload(), SOURCE, provider=provider)

    assert got["name"] == "Imported Recipe"      # the gap stays a gap


def test_a_title_that_is_in_the_text_is_accepted():
    provider = FakeProvider({"name": "Grandma's Lemon Drizzle Cake"})

    got = complete(_payload(), SOURCE, provider=provider)

    assert got["name"] == "Grandma's Lemon Drizzle Cake"


@pytest.mark.parametrize("servings,expected", [
    (8, 8),
    (0, 0),          # not a real answer
    (-3, 0),
    (5000, 0),       # not a household recipe
    ("eight", 0),    # not a number
    (None, 0),
])
def test_servings_are_bounded_since_grounding_cannot_check_a_bare_number(
        servings, expected):
    provider = FakeProvider({"servings": servings})

    got = complete(_payload(), SOURCE, provider=provider)

    assert got["servings"] == expected


# --- Fail open ---------------------------------------------------------------

def test_no_provider_leaves_the_payload_exactly_as_it_was(monkeypatch):
    def no_provider():
        raise RuntimeError("none configured")
    monkeypatch.setattr("app.services.ai.registry.get_provider", no_provider)

    payload = _payload()
    assert complete(payload, SOURCE) == payload


@pytest.mark.parametrize("answer", [None, [], "text", {"steps": "not a list"},
                                    {"steps": [{}]}, {"name": None}])
def test_a_useless_answer_keeps_the_deterministic_result(answer):
    got = complete(_payload(), SOURCE, provider=FakeProvider(answer))

    assert got["name"] == "Imported Recipe"
    assert got["steps"] == []


def test_a_provider_that_raises_never_fails_the_import():
    class Boom:
        def complete_json(self, *a, **k):
            raise RuntimeError("upstream on fire")

    payload = _payload()
    assert complete(payload, SOURCE, provider=Boom()) == payload


def test_no_source_text_means_no_call():
    provider = FakeProvider({"name": "x"})

    complete(_payload(), "", provider=provider)

    assert provider.prompts == []


def test_the_source_is_clamped_into_the_prompt():
    provider = FakeProvider({})

    complete(_payload(), "x " * 100000, provider=provider)

    assert len(provider.prompts[0]) < recipe_complete.MAX_SOURCE_CHARS + 2000


# --- Through the importer ----------------------------------------------------

def test_an_ingredients_only_paste_gets_its_method_filled(auth_client, monkeypatch):
    """The case this exists for: a paste the parser reads exactly for its
    ingredients but which arrives with no title and no method."""
    import app.api.ai as ai_api

    text = ("Lemon Drizzle Cake\n"
            "Ingredients\n225g unsalted butter\n4 large eggs\n225g flour\n")

    class P:
        def complete_json(self, prompt, system=""):
            return {"steps": [{"text": "225g unsalted butter"}], "servings": 8}

    monkeypatch.setattr(ai_api, "get_provider", lambda: P())
    r = auth_client.post("/api/v1/ai/import", json={"text": text})

    assert r.status_code == 201
    body = r.get_json()
    # The deterministic ingredients are untouched...
    assert [i["display"] for i in body["ingredients"]] == [
        "225g unsalted butter", "4 large eggs", "225g flour"]
    # ...and the gap the parser left was filled.
    assert body["servings"] == 8


def test_a_clean_paste_still_imports_with_no_provider_at_all(auth_client, monkeypatch):
    """The completion pass must not have made a provider necessary again."""
    import app.api.ai as ai_api

    def no_provider():
        from app.services.ai.base import ProviderError
        raise ProviderError("none configured")
    monkeypatch.setattr(ai_api, "get_provider", no_provider)

    r = auth_client.post("/api/v1/ai/import", json={"text": SOURCE + "\nMethod\n1. Bake.\n"})

    assert r.status_code == 201


def test_a_step_under_an_unexpected_key_does_not_become_the_word_None():
    """str(None) is "None", which then passed grounding on any source containing
    that word — two literal "None" steps for a model that answered with
    {"instruction": ...}."""
    got = complete(_payload(), "Nut allergens: none\n200g flour\nBake it well.\n",
                   provider=FakeProvider({"steps": [{"instruction": "Mix"},
                                                    {"instruction": "Bake"}]}))

    assert got["steps"] == []


def test_filling_the_method_also_recovers_the_oven_temperature():
    """cookTemperatureC is derived from the steps at PARSE time, so a recipe
    whose method arrives here stored no temperature at all — the one field with
    its own column and converter."""
    got = complete(_payload(cookTemperatureC=None), SOURCE, provider=FakeProvider(
        {"steps": [{"text": "Heat the oven to 180C."}]}))

    assert got["cookTemperatureC"] == 180


def test_an_ai_derived_payload_does_not_pay_for_a_second_call(auth_client, monkeypatch):
    """import_recipe already handed this exact text to the model. Asking again is
    a second call and a second wait for the same answer."""
    import app.api.ai as ai_api

    calls = []

    class Counting:
        def complete_json(self, prompt, system=""):
            calls.append(prompt)
            return {"name": "Chatty Soup", "ingredients": [{"display": "1 onion"}],
                    "steps": [{"text": "Simmer."}], "servings": 0}

    monkeypatch.setattr(ai_api, "get_provider", lambda: Counting())
    # Prose the deterministic parser refuses, so the AI path runs.
    auth_client.post("/api/v1/ai/import",
                     json={"text": "some rambling prose about a soup my aunt made"})

    assert len(calls) == 1, f"paid for {len(calls)} model calls"
