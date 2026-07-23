"""Provision a stable API key for the Home Assistant companion integration.

The integration polls the REST API directly (not via ingress), so it must
authenticate with a long-lived API key rather than rely on the open fallback.
We mint one at startup, bind it to the shared household owner, and persist the
RAW token to a private file in the data dir so that:

* it stays **stable across restarts** — re-minting each boot would invalidate
  the token the configured integration already stored, breaking it; and
* the **discovery step** (a separate process; see ``ha_discovery.py``) can read
  it to hand Home Assistant the token in the discovery payload.

The raw token is a secret: the file is ``0600`` and its value is never logged.
Only a SHA-256 hash is stored in the DB (plus a short display ``hint``).
"""
from __future__ import annotations

import logging
import os

from flask import current_app

_LOGGER = logging.getLogger(__name__)

TOKEN_NAME = "Home Assistant integration"
_RAW_FILENAME = ".integration_token"


def _raw_path() -> str:
    # Single source of truth for the data dir is the resolved settings object
    # (not env), so tests and the running app never disagree about the location.
    return os.path.join(current_app.config["SETTINGS"].data_dir, _RAW_FILENAME)


def ensure_integration_token(app=None) -> str | None:
    """Return the raw integration token, creating it once and reusing it after.

    Pass ``app`` to run against an existing application (tests); otherwise a
    fresh app is built (the startup / discovery path). Best-effort: returns
    ``None`` (logged) if provisioning fails, so discovery can still proceed —
    the integration just falls back to the open path.
    """
    try:
        from app.auth import _default_user
        from app.extensions import db
        from app.models import ApiToken, generate_raw_token, hash_token

        if app is None:
            from app import create_app

            app = create_app()
        with app.app_context():
            path = _raw_path()
            # Reuse a previously-minted token if the raw file still matches a
            # live DB record — keeps the integration's stored token valid.
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    raw = fh.read().strip()
                if raw and (
                    db.session.query(ApiToken)
                    .filter_by(token_hash=hash_token(raw))
                    .first()
                ):
                    return raw

            owner = _default_user()
            raw = generate_raw_token()
            record = ApiToken(
                name=TOKEN_NAME,
                token_hash=hash_token(raw),
                hint=raw[:7] + "…",
                user_id=owner.id,
                group_id=owner.group_id,
            )
            db.session.add(record)
            db.session.commit()

            # Persist raw for restart-stability + discovery handoff (0600).
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(raw)
            _LOGGER.info("Provisioned Home Assistant integration API key (%s…)", raw[:7])
            return raw
    except Exception as exc:  # noqa: BLE001 - best effort; never block startup
        _LOGGER.warning("Integration token provisioning failed (non-fatal): %s", exc)
        return None
