"""RFC 5545 conformance for the hand-rolled ICS emitter.

We own this emitter instead of taking a dependency, so these tests carry the
weight the library's own test-suite would have. They target the three things
naive emitters get wrong — escaping, octet-based folding, and the all-day
DTEND-is-exclusive rule — rather than re-asserting that string concatenation
works.
"""
from datetime import date, datetime, timezone

from app.services.ics import all_day_event, calendar, escape_text, fold


# --------------------------------------------------------------------------
# Escaping (§3.3.11)
# --------------------------------------------------------------------------

def test_escapes_the_four_special_characters():
    assert escape_text("a,b") == "a\\,b"
    assert escape_text("a;b") == "a\\;b"
    assert escape_text("a\\b") == "a\\\\b"
    assert escape_text("a\nb") == "a\\nb"


def test_backslash_is_escaped_before_the_others():
    # If ";" were escaped first, the "\" it introduces would be doubled on the
    # backslash pass and the value would decode as a literal "\;" instead of a
    # semicolon. This ordering bug is invisible until a value contains both.
    assert escape_text("a\\;b") == "a\\\\\\;b"


def test_all_newline_conventions_collapse_to_literal_backslash_n():
    assert escape_text("a\r\nb") == "a\\nb"
    assert escape_text("a\rb") == "a\\nb"
    # A bare CR must never survive into the output: it would fold the line in a
    # way the parser reads as a new content line.
    assert "\r" not in escape_text("a\r\nb\rc")


def test_escape_handles_none_and_non_strings():
    assert escape_text(None) == ""
    assert escape_text(4) == "4"


def test_real_world_recipe_name_round_trips():
    # The kind of name that actually breaks feeds in the wild.
    out = escape_text("Ham, Egg & Chips; Deluxe\nServes 4")
    assert out == "Ham\\, Egg & Chips\\; Deluxe\\nServes 4"


# --------------------------------------------------------------------------
# Folding (§3.1)
# --------------------------------------------------------------------------

def test_short_line_is_not_folded():
    assert fold("SUMMARY:Toast") == ["SUMMARY:Toast"]


def test_line_of_exactly_75_octets_is_not_folded():
    line = "X" * 75
    assert fold(line) == [line]


def test_long_line_folds_with_a_leading_space_on_continuations():
    line = "SUMMARY:" + "a" * 200
    parts = fold(line)
    assert len(parts) > 1
    assert parts[0] == line[:75]
    for p in parts[1:]:
        assert p.startswith(" ")
    # Unfolding (drop the CRLF + one leading space) must restore the original.
    assert parts[0] + "".join(p[1:] for p in parts[1:]) == line


def test_every_folded_line_is_within_75_octets():
    line = "DESCRIPTION:" + "wéé " * 100  # multi-byte, so chars != octets
    for p in fold(line):
        assert len(p.encode("utf-8")) <= 75


def test_folding_never_splits_a_multibyte_character():
    # Pure 2-byte characters guarantee a naive byte-slice at 75 lands mid-char.
    line = "SUMMARY:" + "é" * 120
    parts = fold(line)
    for p in parts:
        p.encode("utf-8").decode("utf-8")  # would raise if a char were severed
    assert parts[0] + "".join(p[1:] for p in parts[1:]) == line


def test_folding_is_measured_in_octets_not_characters():
    # 100 two-byte chars = 200 octets. A character-counting emitter would emit
    # two lines; an octet-correct one needs at least three.
    assert len(fold("é" * 100)) >= 3


# --------------------------------------------------------------------------
# All-day VEVENT shape
# --------------------------------------------------------------------------

STAMP = datetime(2026, 9, 12, 8, 30, 0, tzinfo=timezone.utc)


def test_all_day_event_uses_value_date_and_an_exclusive_dtend():
    lines = all_day_event("u1", date(2026, 9, 12), "Chilli", dtstamp=STAMP)
    assert "DTSTART;VALUE=DATE:20260912" in lines
    # DTEND is the NEXT day — exclusive. Equal DTSTART/DTEND is a zero-length
    # event that clients drop or render on the wrong day.
    assert "DTEND;VALUE=DATE:20260913" in lines


def test_all_day_event_crossing_a_month_boundary():
    lines = all_day_event("u1", date(2026, 9, 30), "Stew", dtstamp=STAMP)
    assert "DTSTART;VALUE=DATE:20260930" in lines
    assert "DTEND;VALUE=DATE:20261001" in lines


def test_all_day_event_structure_and_transparency():
    lines = all_day_event("u1", date(2026, 9, 12), "Chilli", dtstamp=STAMP)
    assert lines[0] == "BEGIN:VEVENT"
    assert lines[-1] == "END:VEVENT"
    assert "UID:u1" in lines
    assert "DTSTAMP:20260912T083000Z" in lines
    assert "SUMMARY:Chilli" in lines
    # A meal plan must not mark the whole day busy in a shared work calendar.
    assert "TRANSP:TRANSPARENT" in lines


def test_description_is_omitted_when_empty_rather_than_emitted_blank():
    lines = all_day_event("u1", date(2026, 9, 12), "Chilli", dtstamp=STAMP)
    assert not any(line.startswith("DESCRIPTION") for line in lines)
    with_desc = all_day_event("u1", date(2026, 9, 12), "Chilli", "Serves 4",
                              dtstamp=STAMP)
    assert "DESCRIPTION:Serves 4" in with_desc


def test_summary_is_escaped_inside_the_event():
    lines = all_day_event("u1", date(2026, 9, 12), "Beans, on toast; hot",
                          dtstamp=STAMP)
    assert "SUMMARY:Beans\\, on toast\\; hot" in lines


def test_naive_dtstamp_is_treated_as_utc():
    lines = all_day_event("u1", date(2026, 9, 12), "X",
                          dtstamp=datetime(2026, 9, 12, 8, 30, 0))
    assert "DTSTAMP:20260912T083000Z" in lines


# --------------------------------------------------------------------------
# VCALENDAR wrapper
# --------------------------------------------------------------------------

def test_calendar_wraps_events_and_declares_the_required_properties():
    out = calendar([all_day_event("u1", date(2026, 9, 12), "Chilli",
                                  dtstamp=STAMP)])
    assert out.startswith("BEGIN:VCALENDAR\r\n")
    assert out.endswith("END:VCALENDAR\r\n")
    for required in ("VERSION:2.0", "PRODID:-//myMeal//Meal plan//EN",
                     "CALSCALE:GREGORIAN"):
        assert f"\r\n{required}\r\n" in out
    assert "BEGIN:VEVENT" in out and "END:VEVENT" in out


def test_every_line_ends_with_crlf_and_none_with_a_bare_lf():
    out = calendar([all_day_event("u1", date(2026, 9, 12), "Chilli",
                                  dtstamp=STAMP)])
    # Strip the CRLFs; if any LF remains it was a bare one.
    assert "\n" not in out.replace("\r\n", "")
    assert out.count("\r\n") == out.count("\n")


def test_calendar_name_is_escaped_and_present():
    out = calendar([], name="Meals, plans")
    assert "X-WR-CALNAME:Meals\\, plans" in out


def test_empty_calendar_is_still_valid():
    # A household with no planned meals must yield a parseable empty feed, not
    # a 404 or a truncated file — subscribers poll this on a timer.
    out = calendar([])
    assert out.startswith("BEGIN:VCALENDAR\r\n")
    assert out.endswith("END:VCALENDAR\r\n")
    assert "BEGIN:VEVENT" not in out
