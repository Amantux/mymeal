"""Step structure: phase headings and the hidden clock.

Pure-string tests — no app, no db, no fixtures. The focaccia method is the real
case this module was built for and is used verbatim: it hides 12-72 hours of
fridge inside step 4 and 2 hours of proof inside step 7, and reads as twelve
undifferentiated sentences.
"""
from app.services.step_structure import (
    find_durations,
    human_minutes,
    human_span,
    infer_phase,
    structure_steps,
)

FOCACCIA = [
    "Mix flour, salt, yeast, water, and 1 tablespoon olive oil into a shaggy "
    "dough until no dry flour remains.",
    "Cover and rest 5 minutes. Perform a stretch-and-fold by pulling dough from "
    "one side over itself. Rotate and repeat 4 times.",
    "Repeat folding process 3 more times with 5-minute rests between each round.",
    "Cover and let rest 40 minutes, then refrigerate for 12–72 hours.",
    "Pour 1 1/2 tablespoons olive oil into a cast iron pan. Transfer dough and "
    "coat both sides.",
    "Press dough into pan. If it resists, rest 15 minutes and repeat until it "
    "reaches edges.",
    "Cover and let rise at room temperature for 2 hours until puffy.",
    "Preheat oven to 450°F (232°C) with racks at bottom and top.",
    "Top dough with mozzarella first, spreading edge to edge. Dollop sauce on "
    "top (do not spread).",
    "Bake on bottom rack for 18–20 minutes until crust is golden and crispy.",
    "Move to top rack for 2–4 minutes if needed to brown the top.",
    "Finish with grated cheese and fresh herbs. Let cool briefly, then serve",
]


def _steps(texts, titles=None):
    titles = titles or [""] * len(texts)
    return [{"title": t, "text": x} for t, x in zip(titles, texts)]


def _phases(result):
    return [g["title"] for g in result["groups"]]


# ---------------------------------------------------------------- durations

def test_finds_plain_duration():
    d = find_durations("Cover and rest 5 minutes.")
    assert len(d) == 1
    assert (d[0]["lo"], d[0]["hi"], d[0]["unit"]) == ("5", None, "min")
    assert d[0]["loMinutes"] == d[0]["hiMinutes"] == 5
    assert d[0]["label"] == "5 min"


def test_range_stays_a_range():
    """12-72 hours must never collapse to a single number — the whole point of
    surfacing it is that the cook chooses where in that window to bake."""
    d = find_durations("refrigerate for 12–72 hours")[0]
    assert d["lo"] == "12" and d["hi"] == "72"
    assert (d["loMinutes"], d["hiMinutes"]) == (720, 4320)
    assert d["label"] == "12–72 hr"


def test_hyphenated_adjective_duration():
    """"5-minute rests" — the hyphen is not a range separator here."""
    d = find_durations("Repeat 3 more times with 5-minute rests between rounds.")
    assert [x["label"] for x in d] == ["5 min"]


def test_range_separators():
    """Four ways to write a range, one chip. The separator is normalised to an
    en dash so chips read the same down the list; the NUMBERS are the author's."""
    for text, span, label in [
        ("bake 18–20 minutes", (18, 20), "18–20 min"),
        ("bake 18-20 minutes", (18, 20), "18–20 min"),
        ("bake 18 to 20 minutes", (18, 20), "18–20 min"),
        ("rest 2 or 3 hours", (120, 180), "2–3 hr"),
    ]:
        d = find_durations(text)
        assert len(d) == 1, text
        assert (d[0]["loMinutes"], d[0]["hiMinutes"]) == span, text
        assert d[0]["label"] == label, text


def test_units_and_fractions():
    assert find_durations("rest 30 seconds")[0]["loMinutes"] == 1  # floored to 1
    assert find_durations("chill 1.5 hours")[0]["loMinutes"] == 90
    assert find_durations("rest 1 1/2 hours")[0]["loMinutes"] == 90
    assert find_durations("ferment 3 days")[0]["loMinutes"] == 4320
    assert find_durations("rest 1 day")[0]["unit"] == "day"


def test_numbers_without_a_time_unit_are_never_durations():
    """The mandatory unit is the only defence against a method full of numbers."""
    for text in [
        "Pour 1 1/2 tablespoons olive oil into a cast iron pan.",
        "Rotate and repeat 4 times.",
        "Preheat oven to 450°F (232°C) with racks at bottom and top.",
        "Mix 500 g flour with 2 teaspoons salt.",
        "Use a 9 inch pan and see page 42.",
    ]:
        assert find_durations(text) == [], text


def test_nothing_is_invented_for_overnight():
    """"Overnight" has no number in it, so it gets no minutes. It still reads as
    a Chill phase — the heading is inference, the clock is not."""
    assert find_durations("Cover and leave overnight.") == []
    assert infer_phase("Cover and leave overnight.") == "Chill"


# ---------------------------------------------------------------- wait vs cook

def test_duration_kind_from_nearest_cue():
    assert find_durations("rest 40 minutes")[0]["kind"] == "wait"
    assert find_durations("refrigerate for 12–72 hours")[0]["kind"] == "wait"
    assert find_durations("Bake for 18–20 minutes")[0]["kind"] == "cook"
    assert find_durations("Move to top rack for 2–4 minutes")[0]["kind"] == "active"


def test_cue_is_scoped_to_its_own_sentence():
    """A step-wide cue search called this rest a bake, because "Preheat" sits
    in the previous sentence."""
    d = find_durations("Preheat the oven to 200C. Rest the dough 10 minutes.")
    assert [x["kind"] for x in d] == ["wait"]


def test_two_durations_in_one_sentence_classify_independently():
    d = find_durations("Cover and let rest 40 minutes, then refrigerate for 12–72 hours.")
    assert [x["kind"] for x in d] == ["wait", "wait"]
    assert [x["label"] for x in d] == ["40 min", "12–72 hr"]


# ---------------------------------------------------------------- temperature

def test_temperature_comes_from_the_shared_parser():
    row = structure_steps(_steps([FOCACCIA[7]]))["steps"][0]
    assert row["temperatureC"] == 232.2
    # Formatted by services.cooking, which rounds to oven-dial granularity and
    # shows both scales because a household has one oven with one set of marks.
    assert row["temperature"] == "230°C / 450°F"


def test_no_temperature_is_empty_not_zero():
    row = structure_steps(_steps(["Mix the flour and water."]))["steps"][0]
    assert row["temperature"] == "" and row["temperatureC"] is None


# ---------------------------------------------------------------- phases

def test_focaccia_groups_into_phases_a_baker_recognises():
    result = structure_steps(_steps(FOCACCIA))
    assert _phases(result) == [
        "Mix", "Fold", "Chill", "Shape", "Proof", "Top & bake", "Finish",
    ]
    assert [g["indexes"] for g in result["groups"]] == [
        [0], [1, 2], [3], [4, 5], [6], [7, 8, 9, 10], [11],
    ]
    assert all(g["derived"] for g in result["groups"])


def test_chill_outranks_rest_in_the_same_step():
    """Step 4 says both "rest 40 minutes" and "refrigerate 12-72 hours". It is a
    cold ferment; priority order, not text order, decides."""
    assert infer_phase(FOCACCIA[3]) == "Chill"


def test_unmatched_step_carries_the_previous_phase():
    """"Move to top rack for 2-4 minutes" names no phase. Inheriting "Top &
    bake" is right; inventing a new heading would not be."""
    result = structure_steps(_steps(FOCACCIA))
    bake = [g for g in result["groups"] if g["title"] == "Top & bake"][0]
    assert 10 in bake["indexes"]


def test_author_titles_win_outright():
    titles = ["Dough", "Dough", "", "", "The bake", "The bake"]
    texts = ["Mix it.", "Knead it.", "Rest it.", "Chill it.", "Bake it.", "Serve it."]
    result = structure_steps(_steps(texts, titles))
    assert _phases(result) == ["Dough", "", "The bake"]
    assert not any(g["derived"] for g in result["groups"])
    assert result["summary"]["derivedHeadings"] is False


def test_short_methods_are_not_grouped():
    result = structure_steps(_steps(["Mix it.", "Bake it.", "Serve it."]))
    assert _phases(result) == [""]
    assert result["summary"]["grouped"] is False


def test_grouping_is_suppressed_when_every_step_is_its_own_phase():
    """A heading per step is strictly worse than the flat list it replaced."""
    texts = [
        "Chop the onion.", "Mix the batter.", "Shape it in the tin.",
        "Bake it.", "Serve it.",
    ]
    result = structure_steps(_steps(texts))
    assert _phases(result) == [""]
    assert result["summary"]["grouped"] is False


def test_single_phase_is_not_a_heading():
    texts = ["Mix the flour.", "Mix in water.", "Stir well.", "Combine fully.",
             "Blend smooth."]
    result = structure_steps(_steps(texts))
    assert _phases(result) == [""]


# ---------------------------------------------------------------- the summary

def test_focaccia_summary_surfaces_the_hidden_clock():
    summary = structure_steps(_steps(FOCACCIA))["summary"]
    # 5 + 5 + 40 + (720–4320) + 15 + 120  — every number stated in the text.
    assert summary["waitMinutes"] == {"lo": 905, "hi": 4505}
    assert summary["waitText"] == "15 hr 5 min – 3 days 3 hr"
    assert summary["cookMinutes"] == {"lo": 18, "hi": 20}
    assert summary["cookText"] == "18–20 min"
    assert summary["activeMinutes"] == {"lo": 2, "hi": 4}
    assert summary["waitingSteps"] == 5
    # The honest caveat: step 3 repeats a 5-minute rest three times without
    # restating it, so we count 5 and say "at least" rather than multiplying.
    assert summary["atLeast"] is True


def test_summary_is_empty_when_the_text_states_no_times():
    summary = structure_steps(_steps(["Mix it.", "Bake it.", "Serve it."]))["summary"]
    assert summary["totalMinutes"] == {"lo": 0, "hi": 0}
    assert summary["totalText"] == ""
    assert summary["atLeast"] is False


def test_step_text_is_returned_byte_identical():
    """The one invariant that must never break: we add headings and chips, we do
    not edit the cook's words."""
    rows = structure_steps(_steps(FOCACCIA))["steps"]
    assert [r["text"] for r in rows] == FOCACCIA


def test_empty_input_is_safe():
    result = structure_steps([])
    assert result["steps"] == []
    assert result["summary"]["grouped"] is False


# ---------------------------------------------------------------- formatting

def test_human_minutes():
    assert human_minutes(0) == ""
    assert human_minutes(40) == "40 min"
    assert human_minutes(60) == "1 hr"
    assert human_minutes(125) == "2 hr 5 min"
    assert human_minutes(1440) == "1 day"
    assert human_minutes(4505) == "3 days 3 hr"


def test_human_span_collapses_when_ends_agree():
    assert human_span(120, 120) == "2 hr"
    assert human_span(0, 0) == ""


def test_human_span_states_a_shared_unit_once():
    assert human_span(18, 20) == "18–20 min"
    assert human_span(120, 180) == "2–3 hr"
    # No shared unit to factor out, so both sides stay whole.
    assert human_span(905, 4505) == "15 hr 5 min – 3 days 3 hr"
