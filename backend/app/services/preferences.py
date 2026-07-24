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
