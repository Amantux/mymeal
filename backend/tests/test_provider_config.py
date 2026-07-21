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
