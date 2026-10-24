"""Household food preferences — diet, allergies, dislikes, free notes.

Stored per-group in the Setting KV table (namespaced ``pref_*``). The AI recipe
draft and the chat assistant read these so suggestions respect the household's
diet and never include an allergen. The assistant can also update them ("I'm
vegetarian now"), so preferences are learned over time, not just set once.
"""
from ..extensions import db
from ..models import Setting

FIELDS = ("diet", "allergies", "dislikes", "notes")
_PREFIX = "pref_"

#: How a recipe's amounts are shown when the caller does not say.
#:
#: Defaults to "weight" deliberately, which is a change from the old
#: show-as-written behaviour: a cup of flour varies ~20% with how it was
#: scooped, so for dry bulk goods the weight is the more reproducible number
#: and the one a recipe should lead with.
#:
#: Stored per household rather than per request because it is a kitchen fact
#: (do you own a scale?) that one person settles once, not a per-view toggle.
#: An explicit ``?units=`` on the request still wins, so a client that wants
#: the other rendering is never locked out.
#:
#: Note this only ever ANNOTATES -- the original measure is always kept and the
#: weight is appended. Nothing is rewritten or lost, which is what makes
#: flipping the default safe: a household with no scale sees "1 cup flour
#: (125 g)" and can ignore the parenthetical.
MEASURE_BIAS_VALUES = ("weight", "as_written")
MEASURE_BIAS_DEFAULT = "weight"


def get_preferences(gid: str) -> dict:
    rows = {s.key: s.value for s in db.session.query(Setting).filter_by(group_id=gid).all()}
    return {f: rows.get(_PREFIX + f, "") for f in FIELDS}


def _upsert(gid: str, key: str, value: str) -> None:
    row = db.session.query(Setting).filter_by(group_id=gid, key=key).first()
    if row:
        row.value = value
    else:
        db.session.add(Setting(group_id=gid, key=key, value=value))


def set_preferences(gid: str, data: dict) -> dict:
    """Update only the provided fields (each capped). Returns the full set."""
    for f in FIELDS:
        if f in data:
            _upsert(gid, _PREFIX + f, str(data.get(f) or "").strip()[:500])
    return get_preferences(gid)


def measure_bias(gid: str) -> str:
    """``"weight"`` or ``"as_written"`` — how to render amounts by default.

    Anything unrecognised in storage falls back to the default rather than
    raising: this is read on every recipe GET, and a typo in one household's
    row must not break their recipe view.
    """
    row = (
        db.session.query(Setting)
        .filter_by(group_id=gid, key=_PREFIX + "measure_bias")
        .first()
    )
    value = (row.value if row else "") or ""
    return value if value in MEASURE_BIAS_VALUES else MEASURE_BIAS_DEFAULT


def set_measure_bias(gid: str, value: str) -> str:
    """Set the household's rendering bias. Returns the stored value.

    An unrecognised value is rejected rather than stored, so the read path
    above never has to reason about junk it put there itself.
    """
    if value not in MEASURE_BIAS_VALUES:
        raise ValueError(
            f"measure_bias must be one of {MEASURE_BIAS_VALUES}, got {value!r}")
    _upsert(gid, _PREFIX + "measure_bias", value)
    return value


def preferences_text(gid: str) -> str:
    """A compact one-line summary for injecting into an AI prompt, or '' if none."""
    p = get_preferences(gid)
    parts = []
    if p["diet"]:
        parts.append(f"diet: {p['diet']}")
    if p["allergies"]:
        parts.append(f"allergies to STRICTLY avoid: {p['allergies']}")
    if p["dislikes"]:
        parts.append(f"dislikes: {p['dislikes']}")
    if p["notes"]:
        parts.append(p["notes"])
    return "; ".join(parts)
