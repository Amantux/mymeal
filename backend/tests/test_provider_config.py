"""AI provider config: configurable via env/add-on OR the UI, and remembered.

Mirrors Edibl: non-empty DB overrides win over env; blank falls back; secrets
are write-only.
"""
import json


def _put(client, body):
    return client.put("/api/v1/ai/settings", json=body)


def test_get_settings_reflects_env_default(noauth_app):
    """With MYMEAL set in the (test) env-derived settings, the UI view shows it."""
    app = noauth_app
    with app.app_context():
        app.config["SETTINGS"].values["AI_PROVIDER"] = "ollama"
        app.config["SETTINGS"].values["OLLAMA_HOST"] = "http://envhost:11434"
    c = app.test_client()
    body = c.get("/api/v1/ai/settings").get_json()
    assert body["provider"] == "ollama"
    assert body["baseUrl"] == "http://envhost:11434"


def test_ui_override_wins_over_env(noauth_app):
    app = noauth_app
    with app.app_context():
        app.config["SETTINGS"].values["AI_PROVIDER"] = "ollama"
    c = app.test_client()
    r = _put(c, {"provider": "claude", "model": "claude-opus-4-8", "apiKey": "sk-secret"})
    assert r.status_code == 200
    assert r.get_json()["provider"] == "claude"
    # And it persists on a fresh read.
    assert c.get("/api/v1/ai/settings").get_json()["provider"] == "claude"


def test_api_key_is_never_returned(noauth_app):
    c = noauth_app.test_client()
    _put(c, {"provider": "openai", "apiKey": "sk-must-not-leak-123"})
    blob = json.dumps(c.get("/api/v1/ai/settings").get_json())
    assert "sk-must-not-leak-123" not in blob
    assert c.get("/api/v1/ai/settings").get_json()["apiKeySet"] is True


def test_blank_apikey_on_resave_keeps_the_stored_key(noauth_app):
    """Re-saving the form (which never receives the key back) must not wipe it."""
    c = noauth_app.test_client()
    _put(c, {"provider": "openai", "apiKey": "sk-keep-me"})
    _put(c, {"provider": "openai", "model": "gpt-4o", "apiKey": ""})  # blank key
    assert c.get("/api/v1/ai/settings").get_json()["apiKeySet"] is True


def test_clearing_provider_falls_back_to_env(noauth_app):
    app = noauth_app
    with app.app_context():
        app.config["SETTINGS"].values["AI_PROVIDER"] = "ollama"
    c = app.test_client()
    _put(c, {"provider": "claude"})
    assert c.get("/api/v1/ai/settings").get_json()["provider"] == "claude"
    _put(c, {"provider": ""})   # cleared -> env default returns
    assert c.get("/api/v1/ai/settings").get_json()["provider"] == "ollama"


def test_unknown_provider_is_rejected(noauth_app):
    r = _put(noauth_app.test_client(), {"provider": "gemini"})
    assert r.status_code == 422


def test_settings_endpoints_require_auth(client):
    assert client.get("/api/v1/ai/settings").status_code == 401
    assert client.put("/api/v1/ai/settings", json={}).status_code == 401
    assert client.post("/api/v1/ai/models", json={}).status_code == 401


def test_ha_option_configures_a_provider_key(tmp_path):
    """A pure-HA user can set the provider + key via add-on options.json."""
    p = tmp_path / "options.json"
    p.write_text(json.dumps({"ai_provider": "openai", "openai_api_key": "sk-from-ha"}))
    from app.settings import load_settings
    s = load_settings(env={}, ha_options_path=str(p))
    assert s.AI_PROVIDER == "openai"
    assert s.OPENAI_API_KEY == "sk-from-ha"


def test_switching_provider_drops_the_previous_providers_key(noauth_app):
    """Cross-provider key bleed guard: configure OpenAI with a key, switch to
    Claude without a new key — the OpenAI key must NOT become Claude's key."""
    from app.services.ai.provider_config import effective_settings
    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/settings", json={"provider": "openai", "apiKey": "sk-openai-only"})
    # Switch provider, no new key supplied.
    c.put("/api/v1/ai/settings", json={"provider": "claude"})
    with app.app_context():
        from app.models import Group
        from app.extensions import db
        gid = db.session.query(Group).first().id
        eff = effective_settings(app.config["SETTINGS"], gid)
        assert eff.AI_PROVIDER == "claude"
        assert eff.ANTHROPIC_API_KEY != "sk-openai-only"   # did NOT bleed over


def test_baseurl_must_be_http(noauth_app):
    r = noauth_app.test_client().put(
        "/api/v1/ai/settings", json={"provider": "ollama", "baseUrl": "file:///etc/passwd"})
    assert r.status_code == 422


def test_switching_back_and_forth_keeps_each_providers_key(noauth_app):
    """Per-provider storage: OpenAI key and Claude key coexist; switching does
    not overwrite or leak either."""
    from app.services.ai.provider_config import effective_settings
    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/settings", json={"provider": "openai", "apiKey": "sk-openai"})
    c.put("/api/v1/ai/settings", json={"provider": "claude", "apiKey": "sk-anthropic"})
    with app.app_context():
        from app.models import Group
        from app.extensions import db
        gid = db.session.query(Group).first().id
        base = app.config["SETTINGS"]
        assert effective_settings(base, gid).ANTHROPIC_API_KEY == "sk-anthropic"
    c.put("/api/v1/ai/settings", json={"provider": "openai"})
    with app.app_context():
        from app.models import Group
        from app.extensions import db
        gid = db.session.query(Group).first().id
        eff = effective_settings(app.config["SETTINGS"], gid)
        assert eff.OPENAI_API_KEY == "sk-openai"


def test_clear_key_removes_it(noauth_app):
    c = noauth_app.test_client()
    c.put("/api/v1/ai/settings", json={"provider": "openai", "apiKey": "sk-wrong"})
    assert c.get("/api/v1/ai/settings").get_json()["apiKeySet"] is True
    c.put("/api/v1/ai/settings", json={"clearApiKey": True})
    assert c.get("/api/v1/ai/settings").get_json()["apiKeySet"] is False


def test_list_models_does_not_persist(noauth_app):
    c = noauth_app.test_client()
    c.put("/api/v1/ai/settings", json={"provider": "ollama", "baseUrl": "http://saved:11434"})
    c.post("/api/v1/ai/models", json={"provider": "openai", "baseUrl": "http://probe:1234"})
    body = c.get("/api/v1/ai/settings").get_json()
    assert body["provider"] == "ollama" and body["baseUrl"] == "http://saved:11434"


def test_provider_config_is_isolated_between_groups(app):
    from app.services.ai.provider_config import set_overrides, effective_settings
    with app.app_context():
        from app.models import Group
        from app.extensions import db
        g1 = Group(name="A")
        g2 = Group(name="B")
        db.session.add_all([g1, g2])
        db.session.commit()
        set_overrides(g1.id, provider="openai", api_key="sk-group1")
        set_overrides(g2.id, provider="ollama", base_url="http://g2:11434")
        base = app.config["SETTINGS"]
        assert effective_settings(base, g1.id).AI_PROVIDER == "openai"
        assert effective_settings(base, g2.id).AI_PROVIDER == "ollama"
        assert effective_settings(base, g1.id).OPENAI_API_KEY == "sk-group1"


def test_ollama_cloud_selectable_defaults_hosted_and_requires_key(noauth_app):
    """Ollama Cloud is a provider option; defaults to the hosted host and is
    unavailable until a key is set (asserted through the API, which carries the
    group context)."""
    c = noauth_app.test_client()
    _put(c, {"provider": "ollama_cloud", "model": "gpt-oss:20b"})

    view = c.get("/api/v1/ai/settings").get_json()
    assert view["provider"] == "ollama_cloud"
    assert view["baseUrl"] == "https://ollama.com"   # hosted default
    assert view["apiKeySet"] is False
    assert "ollama_cloud" in view["validProviders"]

    provs = {p["name"]: p for p in c.get("/api/v1/ai/providers").get_json()["providers"]}
    assert provs["ollama_cloud"]["active"] is True
    assert provs["ollama_cloud"]["available"] is False   # no key yet

    _put(c, {"apiKey": "olc-secret"})
    provs = {p["name"]: p for p in c.get("/api/v1/ai/providers").get_json()["providers"]}
    assert provs["ollama_cloud"]["available"] is True    # key set → available


def test_ollama_cloud_key_stored_separately_from_local_ollama(noauth_app):
    """Per-provider namespacing: a cloud key never bleeds into local Ollama."""
    c = noauth_app.test_client()
    _put(c, {"provider": "ollama_cloud", "apiKey": "cloud-key"})
    _put(c, {"provider": "ollama"})  # switch to local
    assert c.get("/api/v1/ai/settings").get_json()["apiKeySet"] is False  # local: no key
    _put(c, {"provider": "ollama_cloud"})  # switch back
    assert c.get("/api/v1/ai/settings").get_json()["apiKeySet"] is True   # cloud key remembered


def test_probing_a_provider_you_have_not_saved_yet_uses_that_providers_host(noauth_app):
    """Switching the form to Ollama Cloud and opening the model picker BEFORE
    saving must probe ollama.com — not whatever host the currently-saved
    provider uses.

    The bug: probe_config resolved the effective settings for the SAVED provider
    and then only patched OLLAMA_HOST when it happened to be empty. With a local
    Ollama saved, the host stayed http://localhost:11434, so the cloud model list
    was fetched from a local server that isn't running and came back empty — with
    no error at all, because list_models swallows everything and returns [].
    """
    from app.services.ai.provider_config import OLLAMA_CLOUD_HOST, probe_config

    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/settings", json={"provider": "ollama",
                                       "baseUrl": "http://localhost:11434",
                                       "model": "llama3.2"})
    with app.app_context():
        from app.extensions import db
        from app.models import Group

        gid = db.session.query(Group).first().id
        eff = probe_config(app.config["SETTINGS"], gid, provider="ollama_cloud")

    assert eff.AI_PROVIDER == "ollama_cloud"
    assert eff.OLLAMA_HOST == OLLAMA_CLOUD_HOST, (
        f"probed {eff.OLLAMA_HOST!r} instead of the cloud host — the model "
        f"picker would query the local server and return an empty list")


def test_probing_local_ollama_does_not_inherit_a_saved_cloud_host(noauth_app):
    """The same bug in the other direction: with Ollama Cloud saved, probing
    'ollama' must not send a local-model request to ollama.com."""
    from app.services.ai.provider_config import OLLAMA_CLOUD_HOST, probe_config

    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/settings", json={"provider": "ollama_cloud",
                                       "model": "gpt-oss:120b", "apiKey": "k-test"})
    with app.app_context():
        from app.extensions import db
        from app.models import Group

        gid = db.session.query(Group).first().id
        eff = probe_config(app.config["SETTINGS"], gid, provider="ollama")

    assert eff.AI_PROVIDER == "ollama"
    assert eff.OLLAMA_HOST != OLLAMA_CLOUD_HOST, (
        "probing a local Ollama inherited the saved cloud host")


def test_an_explicit_base_url_in_the_form_still_wins(noauth_app):
    """The form's own value must beat both defaults — that is the whole point of
    probing before saving."""
    from app.services.ai.provider_config import probe_config

    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/settings", json={"provider": "ollama_cloud", "model": "m"})
    with app.app_context():
        from app.extensions import db
        from app.models import Group

        gid = db.session.query(Group).first().id
        eff = probe_config(app.config["SETTINGS"], gid, provider="ollama_cloud",
                           base_url="http://192.168.1.50:11434")

    assert eff.OLLAMA_HOST == "http://192.168.1.50:11434"


def test_a_401_tells_you_to_fix_the_api_key(noauth_app):
    """The default httpx rendering put the URL front and centre — which sent a
    user hunting for a wrong endpoint when the key was simply missing."""
    import httpx

    from app.services.ai.ollama import OllamaCloudProvider

    p = OllamaCloudProvider.__new__(OllamaCloudProvider)
    p.host, p.model, p.timeout, p.api_key = "https://ollama.com", "m", 60, ""
    r = httpx.Response(401, json={"error": "Unauthorized"},
                       request=httpx.Request("POST", "https://ollama.com/api/chat"))
    try:
        r.raise_for_status()
    except httpx.HTTPError as exc:
        msg = p._explain(exc)

    assert "API key" in msg
    assert "/api/chat" not in msg, "the endpoint is never the cause of a 401"
    assert "HTTPStatusError" not in msg, "internal exception class leaked to the user"
    assert "developer.mozilla.org" not in msg


def test_a_404_points_at_the_model_not_the_endpoint(noauth_app):
    """/api/chat exists on both local and cloud (a genuinely missing path returns
    'path not found'), so a 404 here means the model is unavailable."""
    import httpx

    from app.services.ai.ollama import OllamaCloudProvider

    p = OllamaCloudProvider.__new__(OllamaCloudProvider)
    p.host, p.model, p.timeout, p.api_key = "https://ollama.com", "llama3.2", 60, "k"
    r = httpx.Response(404, json={"error": "not found"},
                       request=httpx.Request("POST", "https://ollama.com/api/chat"))
    try:
        r.raise_for_status()
    except httpx.HTTPError as exc:
        msg = p._explain(exc)

    assert "llama3.2" in msg and "model" in msg.lower()


def test_a_failed_model_list_is_logged_not_swallowed(noauth_app, caplog):
    """It still returns [] so the picker renders — but an empty list and a broken
    one used to be indistinguishable from the outside."""
    import logging
    from types import SimpleNamespace

    from app.services.ai.provider_config import list_models

    eff = SimpleNamespace(AI_PROVIDER="ollama_cloud", OLLAMA_HOST="http://127.0.0.1:9",
                          OLLAMA_API_KEY="k", OPENAI_API_KEY="", OPENAI_BASE_URL="")
    with caplog.at_level(logging.WARNING):
        assert list_models(eff, timeout=2) == []

    assert any("model list failed" in r.getMessage() for r in caplog.records), \
        "a broken model list logged nothing — indistinguishable from an empty one"


def test_the_curated_message_is_what_a_real_request_actually_raises(noauth_app, monkeypatch):
    """Wire-level, not helper-level. An earlier version of this file only called
    _explain() directly, so reverting _post to the raw httpx string broke no
    test — the mutation survived and the fix was unprotected."""
    import httpx
    import pytest

    from app.services.ai.base import ProviderError
    from app.services.ai.ollama import OllamaCloudProvider

    p = OllamaCloudProvider.__new__(OllamaCloudProvider)
    p.host, p.model, p.timeout, p.api_key = "https://ollama.com", "m", 60, ""

    def fake_post(*a, **k):
        return httpx.Response(401, json={"error": "Unauthorized"},
                              request=httpx.Request("POST", "https://ollama.com/api/chat"))

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(ProviderError) as ei:
        p._post({"model": "m", "messages": []})

    msg = str(ei.value)
    assert "API key" in msg
    assert "HTTPStatusError" not in msg and "/api/chat" not in msg
