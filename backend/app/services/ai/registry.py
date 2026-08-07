"""Provider selection.

The active provider and its config come from the effective settings: per-group
DB overrides (set in the UI) layered on top of the env / add-on defaults, so a
provider configured in Home Assistant OR in the UI is honored and remembered.
``list_providers`` powers the Settings UI and the ``/ai/providers`` endpoint.
"""
from __future__ import annotations

from .base import AIProvider, ProviderError
from .claude import ClaudeProvider
from .ollama import OllamaCloudProvider, OllamaProvider
from .openai import OpenAIProvider

_REGISTRY: dict[str, type[AIProvider]] = {
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "ollama_cloud": OllamaCloudProvider,
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


# The base-URL field each provider actually dials. claude has no configurable
# endpoint, so it needs no check. ollama_cloud inherits self.host = OLLAMA_HOST
# and its host IS overridable, so it is guarded too — a fixed default alone is
# not a guarantee.
_ENDPOINT_FIELD = {"ollama": "OLLAMA_HOST", "ollama_cloud": "OLLAMA_HOST",
                   "openai": "OPENAI_BASE_URL"}


def _assert_endpoint_ok(name: str, eff) -> None:
    """Refuse a provider whose configured base URL points at a blocked host.

    The point-of-use SSRF guard: /ai/settings validates on save, but a host can
    arrive via env / add-on options / stored group overrides that never touch
    that path. Runs on every provider build, so no completion path is exempt.
    """
    from .url_guard import llm_url_ok
    field = _ENDPOINT_FIELD.get(name)
    if not field:
        return
    ok, err = llm_url_ok(getattr(eff, field, "") or "")
    if not ok:
        raise ProviderError(err or "the configured base URL is not allowed")


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
    _assert_endpoint_ok(name, eff)  # SSRF: validate the base URL at point of use
    provider = _instance(name, eff)
    if not provider.available():
        raise ProviderError(
            f"AI provider '{name}' is selected but not fully configured "
            "(missing API key or host)."
        )
    return provider


def provider_for_group(gid, settings=None, model=None, provider=None,
                       base_url=None, api_key=None) -> AIProvider:
    """Build the provider for a specific group OUTSIDE a request context (e.g. the
    background worker, where ``g.current_group`` isn't set). Resolves that group's
    saved provider overrides explicitly so a UI-configured provider still applies.
    ``provider`` forces a specific provider (else the group's active one); ``model``
    overrides the provider's model for this run."""
    from .provider_config import effective_settings
    from .settings_access import resolved
    eff = effective_settings(resolved(settings), gid, provider=provider)
    # A per-run host/key override — used by background jobs pointed at their own
    # server. Applied to the resolved settings BEFORE the provider is built, so
    # the provider class needs no knowledge of where the values came from.
    if base_url or api_key:
        _apply_endpoint_override(eff, base_url, api_key)
    name = _configured_name(eff)
    if not name:
        raise ProviderError("No AI provider configured.")
    if name not in _REGISTRY:
        raise ProviderError(f"Unknown AI provider '{name}'.")
    _assert_endpoint_ok(name, eff)  # SSRF: validate the base URL at point of use
    provider = _instance(name, eff)
    if not provider.available():
        raise ProviderError(f"AI provider '{name}' is not fully configured.")
    if model:
        provider.model = str(model)[:100]  # per-run model override
    return provider


def _apply_endpoint_override(eff, base_url, api_key) -> None:
    """Point the resolved settings at a different server / key.

    Writes the attribute the SELECTED provider actually reads, so an override
    can never land on the wrong provider's field — the same per-provider
    namespacing rule the stored config follows.
    """
    p = (getattr(eff, "AI_PROVIDER", "") or "").strip()
    if p in ("ollama", "ollama_cloud"):
        if base_url:
            eff.OLLAMA_HOST = base_url
        if api_key:
            eff.OLLAMA_API_KEY = api_key
    elif p == "openai":
        if base_url:
            eff.OPENAI_BASE_URL = base_url
        if api_key:
            eff.OPENAI_API_KEY = api_key
    elif p == "claude" and api_key:
        eff.ANTHROPIC_API_KEY = api_key   # Claude has no configurable base URL


def resolve_job_provider(kind, gid, settings=None, opts=None) -> AIProvider | None:
    """Provider override for a background job of ``kind`` (nutrition / categorize /
    cluster), or None to fall back to the group's configured chat provider.

    Precedence: per-run ``opts`` > the stored async preference for this kind.
    A stored base_url/api_key lets async work run on its own server — typically a
    local box doing the slow jobs while chat stays on something faster.

    Raises ``ProviderError`` if the chosen provider is unavailable."""
    from .provider_config import job_override
    from .url_guard import llm_url_ok

    opts = opts or {}
    pref = job_override(gid, kind)
    provider = opts.get("provider") or pref["provider"]
    model = opts.get("model") or pref["model"]
    base_url = (opts.get("baseUrl") or pref["base_url"] or "").strip()
    api_key = opts.get("apiKey") or pref["api_key"]
    if not (provider or model or base_url or api_key):
        return None

    # Validated at the point of USE, not only where it was saved: this value can
    # also arrive through per-run opts, which never pass the settings guard.
    if base_url:
        ok, err = llm_url_ok(base_url)
        if not ok:
            raise ProviderError(f"the async AI server URL is not allowed: {err}")

    # Only pass provider= when actually switching provider, so a model-only override
    # keeps the original call shape.
    kwargs = {}
    if model:
        kwargs["model"] = model
    if provider:
        kwargs["provider"] = provider
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    return provider_for_group(gid, settings, **kwargs)


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
