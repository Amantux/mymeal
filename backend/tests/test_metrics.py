"""metrics.record keeps numbers numeric, and still defuses log forging."""
from app.services import metrics


def test_numeric_fields_keep_their_type():
    metrics.record("t_num", ms=12, items=3)

    sample = metrics.recent("t_num")[-1]
    assert sample["ms"] == 12 and sample["items"] == 3


def test_boolean_fields_keep_their_type():
    metrics.record("t_bool", ok=False)

    assert metrics.recent("t_bool")[-1]["ok"] is False


def test_summary_reports_durations_rather_than_an_empty_count():
    """summary() selects on isinstance(ms, int); stringified samples made it dead."""
    for ms in (10, 20, 30):
        metrics.record("t_summary", ms=ms)

    summary = metrics.summary("t_summary")

    assert summary["count"] == 3 and summary["maxMs"] == 30


def test_string_fields_are_scrubbed_of_newlines():
    metrics.record("t_str", name="evil\nforged line")

    assert "\n" not in metrics.recent("t_str")[-1]["name"]


def test_long_string_fields_are_truncated():
    metrics.record("t_long", name="x" * 500)

    assert len(metrics.recent("t_long")[-1]["name"]) <= 121


def test_timer_records_an_integer_duration():
    with metrics.Timer("t_timer", name="x"):
        pass

    assert isinstance(metrics.recent("t_timer")[-1]["ms"], int)


def test_timer_mark_records_ms_once():
    with metrics.Timer("t_mark") as timer:
        timer.mark("ttftMs")
        first = timer.fields["ttftMs"]
        timer.mark("ttftMs")

    assert metrics.recent("t_mark")[-1]["ttftMs"] == first
