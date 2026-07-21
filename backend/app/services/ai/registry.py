"""Provider selection.

The active provider and its config come from the effective settings: per-group
DB overrides (set in the UI) layered on top of the env / add-on defaults, so a
provider configured in Home Assistant OR in the UI is honored and remembered.
``list_providers`` powers the Settings UI and the ``/ai/providers`` endpoint.
"""
from __future__ import annotations

from .base import AIProvider, ProviderError
from .claude import ClaudeProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider

_REGISTRY: dict[str, type[AIProvider]] = {
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
}


def _effective(settings=None):
    """Env-derived settings with this group's DB provider overrides merged in.

    The group is taken from the request context when present; outside a request
    (CLI, startup) it is None and only env/add-on config applies.
    """
    from .settings_access import resolved
    from .provider_config import effective_settings

    base = resolved(settings)
    # Group only exists inside a request. Check for it rather than catching
    # everything, so a real DB/session error surfaces instead of silently
    # downgrading to env-only config (and a different provider/key).
    from flask import g, has_request_context

    gid = g.current_group.id if (has_request_context() and getattr(g, "current_group", None)) else None
    return effective_settings(base, gid)


def _instance(name: str, eff) -> AIProvider:
    """Build a provider from the effective config. Not cached: config can
    change at runtime (a UI save) and must take effect on the next request."""
    return _REGISTRY[name](eff)


def _configured_name(eff) -> str:
    return (eff.AI_PROVIDER or "").strip().lower()


def get_provider(settings=None) -> AIProvider:
    """Return the configured, available provider or raise ``ProviderError``."""
    eff = _effective(settings)
    name = _configured_name(eff)
    if not name:
        raise ProviderError(
            "No AI provider configured. Choose one in Settings, or set "
            "MYMEAL_AI_PROVIDER (claude, openai, or ollama)."
        )
    if name not in _REGISTRY:
        raise ProviderError(f"Unknown AI provider '{name}'.")
    provider = _instance(name, eff)
    if not provider.available():
        raise ProviderError(
            f"AI provider '{name}' is selected but not fully configured "
            "(missing API key or host)."
        )
    return provider


def list_providers(settings=None) -> list[dict]:
    """Report every provider, whether it's available, and which is active."""
    eff = _effective(settings)
    active = _configured_name(eff)
    out = []
    for name in _REGISTRY:
        try:
            avail = _instance(name, eff).available()
        except Exception:  # noqa: BLE001 - never let a bad config crash the list
            avail = False
        out.append({"name": name, "available": avail, "active": name == active})
    return out
