"""AI-provider config: DB overrides on top of the env/add-on defaults.

Inspired by the companion Edibl app, hardened to per-provider storage. The user
picks an active provider and sets its base URL / model / API key; those live in
the per-group ``settings`` table and OVERRIDE the env / add-on defaults, so a
provider set in Home Assistant OR in the myMeal UI is remembered either way.

Storage is **per provider** — keys are namespaced ``<provider>_<field>`` (e.g.
``openai_api_key``, ``ollama_base_url``). This is deliberate: a single shared
key slot would let one vendor's secret be sent to another vendor's endpoint
when you switch providers. Each provider's config is remembered independently.

Precedence (per field): non-empty DB override  >  env / add-on default.
"""
from __future__ import annotations

from types import SimpleNamespace

from ...extensions import db
from ...models import Setting

KEY_PROVIDER = "ai_provider"          # which provider is active
FIELDS = ("base_url", "model", "api_key")   # per-provider, namespaced
VALID_PROVIDERS = ("", "claude", "ollama", "openai")
SECRET_FIELD = "api_key"

# Attributes the provider classes read off the settings object. The effective
# object exposes exactly these, so the provider classes need no change.
_PASSTHROUGH = (
    "OLLAMA_HOST", "OLLAMA_MODEL", "CLAUDE_MODEL", "ANTHROPIC_API_KEY",
    "OPENAI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "AI_TIMEOUT_SECONDS",
)


def _pkey(provider: str, field: str) -> str:
    return f"{provider}_{field}"


def _all(gid: str) -> dict:
    return {s.key: s.value for s in
            db.session.query(Setting).filter_by(group_id=gid).all()}


def _set(gid: str, key: str, value: str) -> None:
    row = db.session.query(Setting).filter_by(group_id=gid, key=key).first()
    if row:
        row.value = value
    else:
        db.session.add(Setting(group_id=gid, key=key, value=value))


def active_provider(base, gid: str | None) -> str:
    over = _all(gid) if gid else {}
    return over.get(KEY_PROVIDER) or base.AI_PROVIDER


def set_overrides(gid, provider=None, base_url=None, model=None,
                  api_key=None, clear_api_key=False) -> None:
    """Upsert overrides for the ACTIVE provider.

    ``provider`` (if given) sets which provider is active. base_url / model /
    api_key apply to that provider (or the current active one if provider is
    omitted), stored under namespaced keys so they never cross providers.

    - a field left None is untouched;
    - base_url/model '' clears that field (falls back to env);
    - api_key is written only when a non-empty value is given (so re-saving a
      form that never receives the key back does not wipe it);
    - clear_api_key=True explicitly removes the stored key.
    """
    if provider is not None:
        _set(gid, KEY_PROVIDER, provider.strip())

    # Which provider do the field writes belong to?
    target = (provider.strip() if provider is not None else "") or _all(gid).get(KEY_PROVIDER, "")
    if target:
        if base_url is not None:
            _set(gid, _pkey(target, "base_url"), base_url.strip())
        if model is not None:
            _set(gid, _pkey(target, "model"), model.strip())
        if clear_api_key:
            _set(gid, _pkey(target, "api_key"), "")
        elif api_key:  # non-empty only
            _set(gid, _pkey(target, "api_key"), api_key)
    db.session.commit()


def effective_settings(base, gid: str | None):
    """Env-derived settings with the ACTIVE provider's fields overridden by this
    group's non-empty DB values. Per-provider namespacing means a stored key or
    host for one provider can never leak into another."""
    over = _all(gid) if gid else {}

    def pick(provider, field, fallback):
        v = over.get(_pkey(provider, field))
        return v if v else fallback  # non-empty override wins; else env default

    ns = SimpleNamespace(**{attr: getattr(base, attr) for attr in _PASSTHROUGH})
    ns.AI_PROVIDER = over.get(KEY_PROVIDER) or base.AI_PROVIDER

    if ns.AI_PROVIDER == "ollama":
        ns.OLLAMA_HOST = pick("ollama", "base_url", base.OLLAMA_HOST)
        ns.OLLAMA_MODEL = pick("ollama", "model", base.OLLAMA_MODEL)
        ns.OLLAMA_API_KEY = pick("ollama", "api_key", base.OLLAMA_API_KEY)
    elif ns.AI_PROVIDER == "openai":
        ns.OPENAI_BASE_URL = pick("openai", "base_url", base.OPENAI_BASE_URL)
        ns.OPENAI_MODEL = pick("openai", "model", base.OPENAI_MODEL)
        ns.OPENAI_API_KEY = pick("openai", "api_key", base.OPENAI_API_KEY)
    elif ns.AI_PROVIDER == "claude":
        ns.CLAUDE_MODEL = pick("claude", "model", base.CLAUDE_MODEL)
        ns.ANTHROPIC_API_KEY = pick("claude", "api_key", base.ANTHROPIC_API_KEY)
    return ns


def _active_view(eff) -> dict:
    """The active provider's base_url/model and whether a key is set — for the
    editable UI. NEVER includes the API key value."""
    p = eff.AI_PROVIDER
    if p == "ollama":
        return {"baseUrl": eff.OLLAMA_HOST, "model": eff.OLLAMA_MODEL,
                "apiKeySet": bool(getattr(eff, "OLLAMA_API_KEY", ""))}
    if p == "openai":
        return {"baseUrl": eff.OPENAI_BASE_URL, "model": eff.OPENAI_MODEL,
                "apiKeySet": bool(eff.OPENAI_API_KEY)}
    if p == "claude":
        return {"baseUrl": "", "model": eff.CLAUDE_MODEL,
                "apiKeySet": bool(eff.ANTHROPIC_API_KEY)}
    return {"baseUrl": "", "model": "", "apiKeySet": False}


def settings_view(base, gid: str | None) -> dict:
    """Redacted, UI-facing view of the effective AI config. No secret values."""
    eff = effective_settings(base, gid)
    over = _all(gid) if gid else {}
    return {"provider": eff.AI_PROVIDER, **_active_view(eff),
            "validProviders": list(VALID_PROVIDERS),
            "source": {"provider": "saved" if over.get(KEY_PROVIDER)
                       else ("env" if base.AI_PROVIDER else "unset")}}


def probe_config(base, gid, provider=None, base_url=None, api_key=None):
    """Build a throwaway effective config for a model probe against the values
    currently in the form (not necessarily saved). Falls back to the saved/env
    effective config for anything not supplied."""
    eff = effective_settings(base, gid)
    p = (provider or eff.AI_PROVIDER or "").strip()
    eff.AI_PROVIDER = p
    if p == "ollama" and base_url:
        eff.OLLAMA_HOST = base_url.strip()
    elif p == "openai":
        if base_url:
            eff.OPENAI_BASE_URL = base_url.strip()
        if api_key:
            eff.OPENAI_API_KEY = api_key
    return eff


def list_models(eff, timeout: float = 12.0) -> list[str]:
    """Query the active provider for its model list, for the UI picker.
    Best-effort; returns [] on any error (never raises into a request)."""
    import httpx

    p = eff.AI_PROVIDER
    try:
        with httpx.Client(timeout=timeout) as c:
            if p == "ollama":
                oh = {"Authorization": f"Bearer {eff.OLLAMA_API_KEY}"} \
                    if getattr(eff, "OLLAMA_API_KEY", "") else {}
                r = c.get(f"{eff.OLLAMA_HOST.rstrip('/')}/api/tags", headers=oh)
                r.raise_for_status()
                return sorted(m.get("name", "") for m in r.json().get("models", []) if m.get("name"))
            if p == "openai":
                base_url = (eff.OPENAI_BASE_URL or "https://api.openai.com/v1").rstrip("/")
                h = {"Authorization": f"Bearer {eff.OPENAI_API_KEY}"} if eff.OPENAI_API_KEY else {}
                r = c.get(f"{base_url}/models", headers=h)
                r.raise_for_status()
                return sorted(m.get("id", "") for m in r.json().get("data", []) if m.get("id"))
    except Exception:  # noqa: BLE001 - a model-picker failure must not 500
        return []
    # claude has no list endpoint; the UI falls back to a free-text model field.
    return []
