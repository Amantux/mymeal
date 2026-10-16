"""Making a long flat method readable: phase headings, and the planning
context a cook needs *before* they start.

Two problems, one module.

**The hidden clock.** A focaccia's method says "refrigerate for 12-72 hours" in
step 4 and "rise for 2 hours" in step 7. A cook reading step 1 has no way to see
that this recipe needs three days of calendar, not forty minutes of evening. The
durations are already in the author's text; nothing read them out. So: pull every
stated duration and temperature, work out which ones the cook spends *waiting*,
and hand the caller a per-step chip plus a recipe-level total.

**The undifferentiated list.** Twelve numbered sentences in a row have no shape.
Bakers think in phases (mix, fold, cold ferment, shape, proof, bake, finish), and
a heading every few steps is the cheapest possible wayfinding. ``RecipeStep``
already has a ``title`` column for exactly this — mirroring the ``section``
column on ingredients — so an author's own headings always win. When there are
none we *derive* headings from a fixed verb/cue vocabulary.

Three rules this module will not break:

1. **The author's text is never rewritten.** We add a heading above a step and a
   chip beside it. ``text`` is returned byte-identical, always.
2. **Numbers come out of the text or not at all.** No "overnight ≈ 8 hours", no
   multiplying "repeat 3 more times with 5-minute rests" into 15 minutes. Sums
   are therefore a *floor*, and the caller is told so (``atLeast``).
3. **Deterministic, no model.** Phase inference is a closed vocabulary of cue
   words; a language model would add latency, a failure mode and
   non-determinism in exchange for nothing a regex can't settle. (If that ever
   stops being true, the AI path must fail open to a plain list.)

Pure functions over strings — no Flask, no SQLAlchemy, no I/O — so the whole
thing is unit-testable on literals. The only import is ``cooking``, itself pure,
because temperature parsing already lives there and a second parser would be
exactly the drift the house rule warns about.
"""
from __future__ import annotations

import re

from .cooking import format_temperature, parse_temperature

# --------------------------------------------------------------------------
# Durations
# --------------------------------------------------------------------------

# A number: mixed fraction first ("1 1/2"), then bare fraction, then
# integer/decimal. Order matters — "1 1/2" must not match as a lone "1".
_NUM = r"\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?"

# Minutes per unit. Keys are matched case-insensitively against the longest
# alternative first, so "hours" never matches as "h" + junk.
_UNITS = {
    "second": 1 / 60, "seconds": 1 / 60, "sec": 1 / 60, "secs": 1 / 60,
    "minute": 1.0, "minutes": 1.0, "min": 1.0, "mins": 1.0,
    "hour": 60.0, "hours": 60.0, "hr": 60.0, "hrs": 60.0,
    "day": 1440.0, "days": 1440.0,
}
_UNIT_ALT = "|".join(sorted(_UNITS, key=len, reverse=True))

# Short form shown in the chip, per unit family.
_UNIT_SHORT = {1 / 60: "sec", 1.0: "min", 60.0: "hr", 1440.0: "days"}

# "5 minutes" · "5-minute" · "12-72 hours" · "18 to 20 minutes" · "2 or 3 hrs".
#
# The range group is OPTIONAL and requires a second number, which is what makes
# "5-minute rests" work: the regex tries the hyphen as a range separator, finds
# "minute" where a number must be, backtracks, and lets `[\s-]*` eat the hyphen
# instead. The unit is MANDATORY — that is the whole defence against reading
# "1 1/2 tablespoons olive oil", "repeat 4 times", "racks at bottom and top" or
# "450°F" as durations. A bare number is never a duration.
_DUR_RE = re.compile(
    rf"(?P<lo>{_NUM})"
    rf"(?:\s*(?:[-–—]|\s(?:to|or)\s)\s*(?P<hi>{_NUM}))?"
    rf"[\s-]*(?P<unit>{_UNIT_ALT})\b",
    re.IGNORECASE,
)

# A duration the cook spends waiting with their hands free, vs one where the
# kitchen is doing the work, vs one they stand over. Kept as three kinds rather
# than two because "2 hours of fridge" and "18 minutes of oven" are different
# planning facts, even though neither is hands-on.
_WAIT_CUES = re.compile(
    r"\brest|\brise|\brising\b|\bprove\b|\bproof|\bproving\b|refrigerat|\bfridge\b"
    r"|\bchill|\bferment|marinat|\bsoak|\bcool\b|\bcooling\b|\bstand\b|\bfreez"
    r"|overnight|\bsit\b|\bsitting\b|\bset aside\b",
    re.IGNORECASE,
)
_COOK_CUES = re.compile(
    r"\bbake|\bbaking\b|\broast|\bsimmer|\bbraise|\bsteam|\bboil|\bbroil"
    r"|preheat|\bcook\b|\bcooking\b|\bgrill|\bfry|\bsaut|\bpoach|\bmicrowave",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+")


def _to_minutes(raw: str, per_unit: float) -> int:
    """Whole minutes, rounded, floor of 1 for anything non-zero — a 30-second
    rest is not 0 minutes of the cook's life."""
    raw = raw.strip()
    if " " in raw:  # mixed fraction: "1 1/2"
        whole, frac = raw.split(None, 1)
        num, den = frac.split("/")
        value = float(whole) + float(num) / float(den)
    elif "/" in raw:
        num, den = raw.split("/")
        value = float(num) / float(den)
    else:
        value = float(raw)
    minutes = value * per_unit
    return max(1, int(round(minutes))) if minutes > 0 else 0


def _kind_at(text: str, pos: int) -> str:
    """Classify the duration starting at ``pos`` by the nearest cue word BEFORE
    it in its own sentence, falling back to the whole step.

    Sentence scoping is what keeps "Preheat the oven. Rest the dough 10 minutes"
    honest: a step-wide search would see "preheat" and call that rest a bake.
    """
    head = text[:pos]
    sentence_start = 0
    for m in _SENTENCE_SPLIT.finditer(head):
        sentence_start = m.end()
    for scope in (head[sentence_start:], head, text):
        wait = _last_match(_WAIT_CUES, scope)
        cook = _last_match(_COOK_CUES, scope)
        if wait is None and cook is None:
            continue
        if cook is None or (wait is not None and wait > cook):
            return "wait"
        return "cook"
    return "active"


def _last_match(pattern: re.Pattern, text: str) -> int | None:
    pos = None
    for m in pattern.finditer(text):
        pos = m.start()
    return pos


def find_durations(text: str) -> list[dict]:
    """Every stated duration in one step, in order of appearance.

    ``lo``/``hi`` are the author's own numerals as written, so "12-72 hours"
    stays a range and renders as the cook wrote it. ``loMinutes``/``hiMinutes``
    are the normalised values for arithmetic; for a single duration they are
    equal, which lets every consumer treat a duration as a span and never
    special-case the non-range form.
    """
    out: list[dict] = []
    for m in _DUR_RE.finditer(text or ""):
        per_unit = _UNITS[m.group("unit").lower()]
        lo_min = _to_minutes(m.group("lo"), per_unit)
        hi_min = _to_minutes(m.group("hi"), per_unit) if m.group("hi") else lo_min
        if hi_min < lo_min:  # "20-18 minutes" — trust neither ordering, just sort
            lo_min, hi_min = hi_min, lo_min
        short = _UNIT_SHORT[per_unit]
        if short == "days" and not m.group("hi") and m.group("lo").strip() == "1":
            short = "day"
        out.append({
            "lo": m.group("lo").strip(),
            "hi": (m.group("hi") or "").strip() or None,
            "unit": short,
            "loMinutes": lo_min,
            "hiMinutes": hi_min,
            "kind": _kind_at(text, m.start()),
            # Pre-formatted for the chip so every surface (SPA, HA, MCP) shows
            # the same string instead of three re-implementations of a dash.
            "label": (f"{m.group('lo').strip()}–{m.group('hi').strip()} {short}"
                      if m.group("hi") else f"{m.group('lo').strip()} {short}"),
        })
    return out


def human_minutes(total: int) -> str:
    """Minutes as a cook reads a clock: "40 min", "2 hr 5 min", "3 days 3 hr"."""
    total = int(total)
    if total <= 0:
        return ""
    if total < 60:
        return f"{total} min"
    if total < 1440:
        hours, mins = divmod(total, 60)
        return f"{hours} hr {mins} min" if mins else f"{hours} hr"
    days, rem = divmod(total, 1440)
    hours = rem // 60
    label = f"{days} day" if days == 1 else f"{days} days"
    return f"{label} {hours} hr" if hours else label


_SIMPLE_SPAN = re.compile(r"^(\d+) (min|hr|day|days)$")


def human_span(lo: int, hi: int) -> str:
    """A span, collapsing to one value when the ends agree.

    When both ends are a single unit the unit is stated once — "18–20 min", not
    "18 min–20 min". Mixed spans keep both sides whole, because
    "15 hr 5 min–3 days" has no shared unit to factor out.
    """
    if lo <= 0 and hi <= 0:
        return ""
    if lo == hi:
        return human_minutes(lo)
    low, high = human_minutes(lo), human_minutes(hi)
    a, b = _SIMPLE_SPAN.match(low), _SIMPLE_SPAN.match(high)
    if a and b and a.group(2) == b.group(2):
        return f"{a.group(1)}–{b.group(1)} {b.group(2)}"
    return f"{low}–{high}"


# --------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------

# Ordered by PRIORITY, not by when they appear in a method: the first rule that
# matches anywhere in the step wins. Priority is what resolves a step that names
# two phases — "let rest 40 minutes, then refrigerate for 12-72 hours" is a cold
# ferment, not a rest, so Chill outranks Proof.
#
# Deliberately coarse. A vocabulary fine enough to label every step produces a
# heading per step, which is strictly less readable than the flat list it
# replaced. Toppings live under "Top & bake" because they go on immediately
# before the oven and a lone "Assemble" heading over one step earns nothing.
_PHASE_RULES: tuple[tuple[str, str], ...] = (
    ("Chill", r"refrigerat|\bfridge\b|\bchill|cold[- ]ferment|overnight|\bfreez"),
    ("Proof", r"\brise\b|\brising\b|\bprove\b|\bproof|\bproving\b|\bpuffy\b|doubled"),
    ("Fold", r"\bfold|stretch[- ]and[- ]fold|\bturn the dough\b|\bcoil\b"),
    ("Finish", r"\bserve|\bfinish\b|garnish|\bslice\b|\benjoy\b|let cool|\bcool briefly"),
    ("Top & bake", r"\bbake|\bbaking\b|\boven\b|preheat|\brack\b|\bbroil|\broast"
                   r"|\btop\b.*\bwith\b|dollop|sprinkle|scatter|\blayer\b"),
    ("Shape", r"\btransfer\b|\bpress\b|\bshape\b|\broll\b|dimple|flatten|divide"
              r"|\bpan\b|\btin\b|\btray\b|baking sheet|\bmould\b|\bmold\b"),
    ("Mix", r"\bmix\b|combine|\bstir\b|\bwhisk\b|\bknead\b|\bbeat\b|\bblend\b"
            r"|dissolve|\brub in\b|\bdough\b|\bbatter\b"),
    ("Cook", r"\bcook\b|\bfry\b|\bsaut|\bsimmer\b|\bboil\b|\bsear\b|\bgrill\b"
             r"|\bsteam\b|\bpoach\b"),
    ("Prep", r"\bchop\b|\bdice\b|\bslice\b|\bpeel\b|\bgrate\b|\bmince\b|\bzest\b"
             r"|\btrim\b|\bmeasure\b|\bweigh\b"),
)
_PHASE_RES = tuple((name, re.compile(pat, re.IGNORECASE)) for name, pat in _PHASE_RULES)

# Below this, headings cost more than they give: a four-step method is already
# scannable, and chopping it into three labelled groups is noise.
MIN_STEPS_TO_GROUP = 5


def infer_phase(text: str) -> str:
    """The phase this step belongs to, or "" when no cue matches. An unmatched
    step is NOT guessed — the caller carries the previous phase forward, which
    is nearly always right ("Move to top rack for 2-4 minutes" is still baking)
    and is at least never a fresh invention."""
    for name, pattern in _PHASE_RES:
        if pattern.search(text or ""):
            return name
    return ""


def _author_groups(steps: list[dict]) -> list[dict]:
    """Group consecutive steps sharing a ``title``, exactly as ingredient rows
    group on ``section``. By adjacency, never by sorting: the author's order is
    the recipe's order, and two separate "Dough" phases stay separate."""
    groups: list[dict] = []
    for i, step in enumerate(steps):
        title = (step.get("title") or "").strip()
        if groups and groups[-1]["title"] == title:
            groups[-1]["indexes"].append(i)
        else:
            groups.append({"title": title, "indexes": [i], "derived": False})
    return groups


def _derived_groups(steps: list[dict]) -> list[dict]:
    groups: list[dict] = []
    carried = ""
    for i, step in enumerate(steps):
        phase = infer_phase(step.get("text") or "") or carried
        carried = phase
        if groups and groups[-1]["title"] == phase:
            groups[-1]["indexes"].append(i)
        else:
            groups.append({"title": phase, "indexes": [i], "derived": bool(phase)})
    return groups


def structure_steps(steps: list[dict]) -> dict:
    """The whole step-structure view of a method.

    ``steps`` is ``[{"title": str, "text": str}, ...]`` in order — the same
    shape the update endpoint accepts and the serializer emits.

    Returns ``{"steps": [...], "groups": [...], "summary": {...}}``:

    * ``steps[i]`` echoes the text **unchanged** plus ``durations``,
      ``temperature`` and ``waiting``.
    * ``groups`` are contiguous index runs with a heading; ``derived`` says
      whether the heading is the author's or ours. Empty-titled groups are
      normal and render unheaded.
    * ``summary`` totals the three kinds of time. ``atLeast`` is true whenever a
      duration was found, because a method can repeat a rest without restating
      it and we refuse to multiply on its behalf.
    """
    rows: list[dict] = []
    totals = {"active": [0, 0], "cook": [0, 0], "wait": [0, 0]}
    for step in steps or []:
        text = step.get("text") or ""
        durations = find_durations(text)
        celsius = parse_temperature(text)
        for d in durations:
            bucket = totals[d["kind"]]
            bucket[0] += d["loMinutes"]
            bucket[1] += d["hiMinutes"]
        rows.append({
            "title": (step.get("title") or "").strip(),
            "text": text,
            "durations": durations,
            "temperature": format_temperature(celsius),
            "temperatureC": round(celsius, 1) if celsius is not None else None,
            # "Mostly waiting" is a claim about the step, so it needs a stated
            # wait duration — not merely the word "rest" somewhere in it.
            "waiting": any(d["kind"] == "wait" for d in durations),
        })

    # An author heading anywhere switches the whole method to author mode: once
    # a human has labelled part of their own method, inferring labels for the
    # rest would mix two voices under identical styling.
    has_author_titles = any(r["title"] for r in rows)
    if has_author_titles:
        groups = _author_groups(rows)
    elif len(rows) >= MIN_STEPS_TO_GROUP:
        groups = _derived_groups(rows)
        headed = [g for g in groups if g["title"]]
        # A heading per step conveys nothing the number didn't, and a single
        # heading over everything is a second card title. Either way, fall back
        # to the flat list rather than shipping clutter.
        if len(headed) < 2 or len(groups) >= len(rows):
            groups = [{"title": "", "indexes": list(range(len(rows))), "derived": False}]
    else:
        groups = [{"title": "", "indexes": list(range(len(rows))), "derived": False}]

    wait, cook, active = totals["wait"], totals["cook"], totals["active"]
    total_lo = wait[0] + cook[0] + active[0]
    total_hi = wait[1] + cook[1] + active[1]
    return {
        "steps": rows,
        "groups": groups,
        "summary": {
            "activeMinutes": {"lo": active[0], "hi": active[1]},
            "cookMinutes": {"lo": cook[0], "hi": cook[1]},
            "waitMinutes": {"lo": wait[0], "hi": wait[1]},
            "totalMinutes": {"lo": total_lo, "hi": total_hi},
            "activeText": human_span(*active),
            "cookText": human_span(*cook),
            "waitText": human_span(*wait),
            "totalText": human_span(total_lo, total_hi),
            "waitingSteps": sum(1 for r in rows if r["waiting"]),
            "atLeast": total_hi > 0,
            "grouped": any(g["title"] for g in groups),
            "derivedHeadings": bool(groups) and any(g["derived"] for g in groups),
        },
    }
