"""Edibl connection config: DB overrides on top of the env / add-on defaults.

Same pattern as the AI provider config — the connection to the companion Edibl
app can be set in Home Assistant add-on options OR in the myMeal UI, and is
remembered either way. Non-empty DB values override env; blank falls back.

The token is write-only (never returned). In the common Home Assistant case
BOTH apps run behind ingress with auth disabled, so myMeal reaches Edibl over
the internal Docker network with NO token — discovering the URL is enough.
"""
from __future__ import annotations

from ..extensions import db
from ..models import Setting

KEY_URL = "edibl_url"
KEY_TOKEN = "edibl_token"


def _all(gid: str) -> dict:
    return {s.key: s.value for s in
            db.session.query(Setting).filter_by(group_id=gid).all()}


def _set(gid: str, key: str, value: str) -> None:
    row = db.session.query(Setting).filter_by(group_id=gid, key=key).first()
    if row:
        row.value = value
    else:
        db.session.add(Setting(group_id=gid, key=key, value=value))


def effective(base, gid: str | None) -> dict:
    """Resolved {url, token} — non-empty DB override wins, else env default."""
    over = _all(gid) if gid else {}
    return {
        "url": (over.get(KEY_URL) or base.EDIBL_URL or "").rstrip("/"),
        "token": over.get(KEY_TOKEN) or base.EDIBL_API_TOKEN or "",
    }


def set_config(gid, url=None, token=None, clear_token=False) -> None:
    """Upsert. None = untouched; url '' clears (falls back to env). token is
    written only when non-empty (a blank re-save keeps it); clear_token removes
    it."""
    if url is not None:
        _set(gid, KEY_URL, url.strip().rstrip("/"))
    if clear_token:
        _set(gid, KEY_TOKEN, "")
    elif token:
        _set(gid, KEY_TOKEN, token)
    db.session.commit()


def config_view(base, gid: str | None) -> dict:
    """Redacted view for the UI — never the token value."""
    eff = effective(base, gid)
    over = _all(gid) if gid else {}
    return {
        "url": eff["url"],
        "tokenSet": bool(eff["token"]),
        "configured": bool(eff["url"]),
        "source": {"url": "saved" if over.get(KEY_URL) else ("env" if base.EDIBL_URL else "unset")},
    }
