"""A small, correct iCalendar (RFC 5545) emitter.

Hand-rolled deliberately: nothing in requirements.txt provides one, and pulling
a dependency in for ~80 lines of string formatting is a worse trade than owning
it — especially in an add-on image we keep small. The scope we need is narrow
(all-day VEVENTs, no recurrence, no timezones), which is the part of RFC 5545
that is genuinely simple. The parts that are NOT simple, and that naive
hand-rolled emitters get wrong, are the three this module exists to get right:

1. **Escaping** (3.3.11) — backslash, semicolon, comma and newline are special
   inside a TEXT value. A recipe called "Ham, Egg & Chips; Deluxe" corrupts the
   property structure if pasted in raw.
2. **Folding** (3.1) — lines over 75 OCTETS must be folded. Octets, not
   characters: folding a UTF-8 recipe name by character count can split a
   multi-byte sequence and produce mojibake (or an unparseable file) in strict
   clients.
3. **CRLF** — the spec mandates CRLF line endings. Some clients tolerate LF;
   Home Assistant's Remote Calendar (via `ical`) and iOS are among those that
   are picky, so we always emit CRLF.

Pure functions only — no Flask, no DB. That is what makes the behaviour above
cheap to test exhaustively.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# 75 octets is the RFC limit for a content line INCLUDING the property name.
_MAX_OCTETS = 75


def escape_text(value) -> str:
    """Escape a TEXT property value per RFC 5545 §3.3.11.

    Order matters: the backslash must be doubled FIRST, or the escapes we add
    below would themselves get escaped on a second pass.
    """
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\\", "\\\\")
    s = s.replace(";", "\\;")
    s = s.replace(",", "\\,")
    # Normalise all three newline conventions to the literal two-character
    # sequence \n, which is how TEXT carries a line break.
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    return s


def fold(line: str) -> list[str]:
    """Split one content line into folded lines of <= 75 octets.

    Continuation lines are prefixed with a single space, which the parser
    strips. We measure in encoded bytes and never cut inside a multi-byte UTF-8
    character; the leading space of a continuation counts toward its 75, so the
    payload budget after the first line is 74.
    """
    data = line.encode("utf-8")
    if len(data) <= _MAX_OCTETS:
        return [line]

    out: list[str] = []
    idx = 0
    budget = _MAX_OCTETS
    while idx < len(data):
        end = min(idx + budget, len(data))
        # Back off until `end` sits on a UTF-8 character boundary. Continuation
        # bytes match 0b10xxxxxx; a boundary is any byte that does not.
        while end > idx and end < len(data) and (data[end] & 0xC0) == 0x80:
            end -= 1
        chunk = data[idx:end].decode("utf-8")
        out.append(chunk if not out else " " + chunk)
        idx = end
        budget = _MAX_OCTETS - 1  # the continuation space costs one octet
    return out


def _prop(name: str, value: str) -> list[str]:
    return fold(f"{name}:{value}")


def _dt(d) -> str:
    return d.strftime("%Y%m%d")


def _utcstamp(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def all_day_event(uid: str, day: date, summary: str, description: str = "",
                  dtstamp: datetime | None = None) -> list[str]:
    """One all-day VEVENT.

    All-day is expressed as DTSTART;VALUE=DATE with DTEND set to the NEXT day:
    DTEND is exclusive in RFC 5545, so `DTEND == DTSTART` is a zero-length event
    that several clients (Google among them) either drop or render on the wrong
    day. TRANSP:TRANSPARENT keeps a meal plan from marking the whole day busy in
    a work calendar, which is the difference between this feed being useful and
    it being immediately unsubscribed.
    """
    lines: list[str] = ["BEGIN:VEVENT"]
    lines += _prop("UID", escape_text(uid))
    lines += _prop("DTSTAMP", _utcstamp(dtstamp))
    lines.append(f"DTSTART;VALUE=DATE:{_dt(day)}")
    lines.append(f"DTEND;VALUE=DATE:{_dt(day + timedelta(days=1))}")
    lines += _prop("SUMMARY", escape_text(summary))
    if description:
        lines += _prop("DESCRIPTION", escape_text(description))
    lines.append("TRANSP:TRANSPARENT")
    lines.append("END:VEVENT")
    return lines


def calendar(events: list[list[str]], name: str = "Meal plan",
             prodid: str = "-//myMeal//Meal plan//EN") -> str:
    """Wrap VEVENTs in a VCALENDAR and join with CRLF.

    X-WR-CALNAME/X-WR-CALDESC are non-standard but are what Google, Apple and
    Home Assistant actually read to label a subscribed feed; without them the
    calendar shows up named after its URL.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{prodid}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    lines += _prop("X-WR-CALNAME", escape_text(name))
    for ev in events:
        lines += ev
    lines.append("END:VCALENDAR")
    # Trailing CRLF: the spec's grammar ends every content line with one, and a
    # missing final break trips strict parsers.
    return "\r\n".join(lines) + "\r\n"
