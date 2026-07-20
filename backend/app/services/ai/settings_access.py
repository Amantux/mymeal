"""How AI providers get their configuration.

Providers used to call ``os.environ.get`` in ``__init__`` and were then cached
for the life of the process, so the first app to need AI froze the provider
configuration for every app afterwards. They now take the settings object that
was resolved once at startup.

``resolved()`` prefers, in order:
  1. an explicitly supplied settings object (tests, embedding),
  2. the settings on the current Flask app,
  3. a freshly loaded set (for the MCP server and CLI tools, which have no app).
"""
from __future__ import annotations


def resolved(settings=None):
    if settings is not None:
        return settings

    try:
        from flask import current_app, has_app_context

        if has_app_context():
            from_app = current_app.config.get("SETTINGS")
            if from_app is not None:
                return from_app
    except Exception:  # noqa: BLE001 - never let config lookup break a request
        pass

    from ...settings import load_settings

    return load_settings()
