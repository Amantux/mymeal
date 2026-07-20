"""Provider selection.

The active provider is chosen by ``MYMEAL_AI_PROVIDER`` (falling back to the
Flask config value). Instances are cached per process. ``list_providers``
powers the Settings UI and the ``/ai/providers`` endpoint.
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

def _instance(name: str, settings=None) -> AIProvider:
    """Build a provider from the CURRENT settings.

    Deliberately NOT cached process-wide any more: the old cache meant the
    first app to request AI froze provider configuration for every app in the
    process, so a settings change (or a second app in tests) was ignored.
    Construction only reads already-resolved fields, so it is cheap.
    """
    from .settings_access import resolved

    return _REGISTRY[name](resolved(settings))


def _configured_name(settings=None) -> str:
    from .settings_access import resolved

    return (resolved(settings).AI_PROVIDER or "").strip().lower()


def get_provider(settings=None) -> AIProvider:
    """Return the configured, available provider or raise ``ProviderError``."""
    from .settings_access import resolved

    settings = resolved(settings)
    name = _configured_name(settings)
    if not name:
        raise ProviderError(
            "No AI provider configured. Set MYMEAL_AI_PROVIDER to "
            "claude, openai, or ollama."
        )
    if name not in _REGISTRY:
        raise ProviderError(f"Unknown AI provider '{name}'.")
    provider = _instance(name, settings)
    if not provider.available():
        raise ProviderError(
            f"AI provider '{name}' is selected but not configured "
            "(missing API key or host)."
        )
    return provider


def list_providers(settings=None) -> list[dict]:
    """Report every provider, whether it's available, and which is active."""
    from .settings_access import resolved

    settings = resolved(settings)
    active = _configured_name(settings)
    out = []
    for name in _REGISTRY:
        try:
            avail = _instance(name, settings).available()
        except Exception:  # noqa: BLE001 - never let a bad env crash the list
            avail = False
        out.append({"name": name, "available": avail, "active": name == active})
    return out
