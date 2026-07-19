"""Provider selection.

The active provider is chosen by ``MYMEAL_AI_PROVIDER`` (falling back to the
Flask config value). Instances are cached per process. ``list_providers``
powers the Settings UI and the ``/ai/providers`` endpoint.
"""
from __future__ import annotations

import os

from flask import current_app

from .base import AIProvider, ProviderError
from .claude import ClaudeProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider

_REGISTRY: dict[str, type[AIProvider]] = {
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
}

_INSTANCES: dict[str, AIProvider] = {}


def _instance(name: str) -> AIProvider:
    if name not in _INSTANCES:
        _INSTANCES[name] = _REGISTRY[name]()
    return _INSTANCES[name]


def _configured_name() -> str:
    return (
        os.environ.get("MYMEAL_AI_PROVIDER")
        or (current_app.config.get("AI_PROVIDER") if current_app else "")
        or ""
    ).strip().lower()


def get_provider() -> AIProvider:
    """Return the configured, available provider or raise ``ProviderError``."""
    name = _configured_name()
    if not name:
        raise ProviderError(
            "No AI provider configured. Set MYMEAL_AI_PROVIDER to "
            "claude, openai, or ollama."
        )
    if name not in _REGISTRY:
        raise ProviderError(f"Unknown AI provider '{name}'.")
    provider = _instance(name)
    if not provider.available():
        raise ProviderError(
            f"AI provider '{name}' is selected but not configured "
            "(missing API key or host)."
        )
    return provider


def list_providers() -> list[dict]:
    """Report every provider, whether it's available, and which is active."""
    active = _configured_name()
    out = []
    for name in _REGISTRY:
        try:
            avail = _instance(name).available()
        except Exception:  # noqa: BLE001 - never let a bad env crash the list
            avail = False
        out.append({"name": name, "available": avail, "active": name == active})
    return out
