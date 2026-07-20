"""Tests for the configuration contract.

These assert externally observable behaviour: what a given set of inputs
resolves to, and which combinations refuse to start. They run without any live
service.
"""
import json
import os

import pytest

from app.settings import (
    ConfigError,
    REDACTED,
    ensure_secret_key,
    load_ha_options,
    load_settings,
    parse_bool,
)

GOOD_SECRET = "s" * 40


def L(env=None, **kw):
    """Load settings from an explicit env, never the real process environment."""
    kw.setdefault("ha_options", {})
    return load_settings(env=env or {}, **kw)


# --------------------------------------------------------------- boolean parsing

@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on"])
def test_boolean_true_forms(raw):
    assert parse_bool(raw) is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", " no ", "off"])
def test_boolean_false_forms(raw):
    assert parse_bool(raw) is False


@pytest.mark.parametrize("raw", ["maybe", "", "2", "yes please", "True!", "disabled"])
def test_unknown_boolean_is_rejected_not_silently_false(raw):
    """The dangerous bug this prevents: MYMEAL_DISABLE_AUTH=maybe silently
    becoming False (or worse, a typo'd 'flase' becoming False when the operator
    meant to disable something)."""
    with pytest.raises(ValueError) as exc:
        parse_bool(raw)
    assert "Accepted" in str(exc.value)


def test_invalid_boolean_names_the_variable_and_accepted_values():
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_DISABLE_AUTH": "sure"})
    msg = str(exc.value)
    assert "MYMEAL_DISABLE_AUTH" in msg and "sure" in msg and "true" in msg


# --------------------------------------------------------------- numeric bounds

def test_numeric_out_of_range_is_rejected():
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_PORT": "70000"})
    assert "between 1 and 65535" in str(exc.value)


def test_numeric_non_integer_is_rejected():
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_WORKERS": "two"})
    assert "MYMEAL_WORKERS" in str(exc.value)


def test_all_errors_are_reported_at_once():
    """Fixing a broken deployment one error per restart is miserable."""
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_PORT": "0", "MYMEAL_WORKERS": "999", "MYMEAL_DISABLE_AUTH": "?"})
    assert len(exc.value.errors) == 3


def test_unknown_enum_value_is_rejected():
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_AI_PROVIDER": "gemini"})
    assert "claude" in str(exc.value)


def test_blank_provider_disables_ai_cleanly():
    s = L({})
    assert s.AI_PROVIDER == "" and s.ai_enabled is False


# --------------------------------------------------------------- precedence

def test_env_beats_default():
    assert L({"MYMEAL_PORT": "9000"}).PORT == 9000


def test_ha_option_beats_env():
    """Inside the add-on, options.json is the only surface the operator can
    edit; if the baked-in environment won, the HA UI toggle would do nothing."""
    s = load_settings(env={"MYMEAL_DISABLE_AUTH": "false"},
                      ha_options={"disable_auth": True})
    assert s.DISABLE_AUTH is True
    assert s.sources["DISABLE_AUTH"] == "ha_option"


def test_override_beats_everything():
    s = load_settings(env={"MYMEAL_PORT": "9000"},
                      ha_options={},
                      overrides={"PORT": 1234})
    assert s.PORT == 1234 and s.sources["PORT"] == "override"


def test_empty_env_var_does_not_beat_default():
    """`MYMEAL_AI_PROVIDER=` in a compose file means 'unset', not 'invalid'."""
    assert L({"MYMEAL_OLLAMA_MODEL": ""}).OLLAMA_MODEL == "llama3.1"


def test_empty_ha_option_does_not_clobber_a_real_default():
    s = load_settings(env={}, ha_options={"ai_provider": ""})
    assert s.AI_PROVIDER == ""


def test_ha_json_booleans_are_honoured_as_booleans():
    s = load_settings(env={}, ha_options={"enable_mcp": False})
    assert s.MCP_ENABLED is False


def test_sources_are_reported_for_diagnostics():
    s = load_settings(env={"MYMEAL_PORT": "9000"}, ha_options={"disable_auth": True})
    assert s.sources["PORT"] == "env"
    assert s.sources["DISABLE_AUTH"] == "ha_option"
    assert s.sources["WORKERS"] == "default"


# --------------------------------------------------------------- HA options file

def test_ha_options_absent_is_not_an_error(tmp_path):
    assert load_ha_options(str(tmp_path / "nope.json")) == {}


def test_ha_options_invalid_json_is_actionable(tmp_path):
    p = tmp_path / "options.json"
    p.write_text("{not json")
    with pytest.raises(ConfigError) as exc:
        load_ha_options(str(p))
    assert str(p) in str(exc.value)


def test_ha_options_non_object_is_rejected(tmp_path):
    p = tmp_path / "options.json"
    p.write_text('["a"]')
    with pytest.raises(ConfigError):
        load_ha_options(str(p))


def test_existing_addon_options_still_work(tmp_path):
    """Backward compatibility: an installation whose options.json contains
    exactly today's four fields must keep working untouched."""
    p = tmp_path / "options.json"
    p.write_text(json.dumps({"disable_auth": True, "allow_registration": False,
                             "enable_mcp": True, "ai_provider": ""}))
    s = load_settings(env={}, ha_options_path=str(p))
    assert (s.DISABLE_AUTH, s.ALLOW_REGISTRATION, s.MCP_ENABLED, s.AI_PROVIDER) == \
           (True, False, True, "")


def test_unknown_ha_option_is_ignored_not_fatal(tmp_path):
    """A newer add-on writing an option this build predates must not brick it."""
    p = tmp_path / "options.json"
    p.write_text(json.dumps({"disable_auth": True, "future_option": "x"}))
    assert load_settings(env={}, ha_options_path=str(p)).DISABLE_AUTH is True


# --------------------------------------------------------------- secrets

def test_placeholder_secret_is_refused():
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_SECRET_KEY": "change-me-in-production"})
    assert "placeholder" in str(exc.value)


def test_short_secret_is_refused_when_auth_is_enabled():
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_SECRET_KEY": "short"})
    assert "characters" in str(exc.value)


def test_secret_file_variant_is_supported(tmp_path):
    """Docker secrets mount a file, not an env var."""
    p = tmp_path / "secret"
    p.write_text(GOOD_SECRET + "\n")
    s = L({"MYMEAL_SECRET_KEY_FILE": str(p)})
    assert s.SECRET_KEY == GOOD_SECRET and s.sources["SECRET_KEY"] == "file"


def test_unreadable_secret_file_is_an_error():
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_SECRET_KEY_FILE": "/no/such/file"})
    assert "secret file" in str(exc.value)


def test_secrets_are_redacted_in_effective_output():
    s = L({"MYMEAL_SECRET_KEY": GOOD_SECRET,
           "MYMEAL_OPENAI_API_KEY": "sk-live-must-never-appear",
           "MYMEAL_ANTHROPIC_API_KEY": "sk-ant-must-never-appear"})
    blob = json.dumps(s.redacted())
    assert "sk-live-must-never-appear" not in blob
    assert "sk-ant-must-never-appear" not in blob
    assert GOOD_SECRET not in blob
    assert blob.count(REDACTED) == 3


def test_invalid_secret_value_is_not_echoed_in_the_error():
    """An error message that quotes the bad value leaks it into logs."""
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_JWT_HOURS": "nope", "MYMEAL_SECRET_KEY": GOOD_SECRET})
    assert GOOD_SECRET not in str(exc.value)


def test_generated_secret_persists_across_restarts(tmp_path):
    """The bug this prevents: a fresh random key each boot silently logs every
    user out and voids every issued API token."""
    first, generated = ensure_secret_key({"SECRET_KEY": ""}, str(tmp_path))
    assert generated is True and len(first) >= 32
    second, generated_again = ensure_secret_key({"SECRET_KEY": ""}, str(tmp_path))
    assert second == first and generated_again is False


def test_generated_secret_file_is_owner_only(tmp_path):
    ensure_secret_key({"SECRET_KEY": ""}, str(tmp_path))
    mode = os.stat(tmp_path / ".secret_key").st_mode & 0o777
    assert mode == 0o600


def test_explicit_secret_is_never_overwritten_by_generation(tmp_path):
    value, generated = ensure_secret_key({"SECRET_KEY": GOOD_SECRET}, str(tmp_path))
    assert value == GOOD_SECRET and generated is False
    assert not os.path.exists(tmp_path / ".secret_key")


# --------------------------------------------------------------- unsafe combinations

def test_disable_auth_with_cors_origins_is_fatal():
    """Any website could then drive the API as the local user."""
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_DISABLE_AUTH": "true", "MYMEAL_CORS_ORIGINS": "https://evil.example"})
    assert "unsafe" in str(exc.value)


def test_wildcard_cors_origin_is_fatal():
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_CORS_ORIGINS": "*"})
    assert "may not contain" in str(exc.value)


def test_cors_origin_without_scheme_is_fatal():
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_CORS_ORIGINS": "evil.example"})
    assert "http://" in str(exc.value)


def test_debug_is_fatal_inside_home_assistant():
    with pytest.raises(ConfigError) as exc:
        load_settings(env={"MYMEAL_DEBUG": "true"}, ha_options={"disable_auth": True})
    assert "Home Assistant" in str(exc.value)


def test_mcp_port_colliding_with_app_port_is_fatal():
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_PORT": "7850", "MYMEAL_MCP_PORT": "7850"})
    assert "cannot share a port" in str(exc.value)


def test_disable_auth_outside_ha_without_a_proxy_warns():
    s = L({"MYMEAL_DISABLE_AUTH": "true"})
    assert any("only safe if something in front" in w for w in s.warnings)


def test_disable_auth_behind_ha_ingress_is_clean():
    """The supported HA path must stay warning-free, or operators learn to
    ignore warnings."""
    s = load_settings(env={}, ha_options={"disable_auth": True, "allow_registration": False})
    assert s.DISABLE_AUTH is True
    assert not [w for w in s.warnings if "only safe if" in w]


def test_ha_mode_does_not_require_a_long_secret():
    """Behind ingress the add-on generates and persists its own key; demanding
    the operator invent one would break a working install for no gain."""
    s = load_settings(env={}, ha_options={"disable_auth": True})
    assert s.DISABLE_AUTH is True


def test_many_workers_with_sqlite_warns_but_boots():
    s = L({"MYMEAL_WORKERS": "8"})
    assert s.WORKERS == 8
    assert any("SQLite serialises writes" in w for w in s.warnings)


def test_non_sqlite_database_warns_that_it_is_unsupported():
    s = L({"MYMEAL_DATABASE_URL": "postgresql://u:p@h/db", "MYMEAL_SECRET_KEY": GOOD_SECRET})
    assert any("Only SQLite is tested" in w for w in s.warnings)


def test_database_url_credentials_are_not_leaked_by_warnings():
    s = L({"MYMEAL_DATABASE_URL": "postgresql://user:hunter2@host/db",
           "MYMEAL_SECRET_KEY": GOOD_SECRET})
    assert "hunter2" not in " ".join(s.warnings)


def test_claude_without_key_warns_but_does_not_block_startup():
    """A missing optional-provider key must degrade the feature, not the app."""
    s = L({"MYMEAL_AI_PROVIDER": "claude", "MYMEAL_SECRET_KEY": GOOD_SECRET})
    assert s.AI_PROVIDER == "claude"
    assert any("ANTHROPIC_API_KEY" in w for w in s.warnings)


# --------------------------------------------------------------- derived values

def test_data_dir_is_always_absolute():
    assert os.path.isabs(L({"MYMEAL_DATA_DIR": "./data"}).data_dir)


def test_resolution_creates_no_directories(tmp_path):
    """Importing or resolving config must never write to disk; that is how data
    ends up in an unexpected working directory."""
    target = tmp_path / "should-not-exist"
    s = L({"MYMEAL_DATA_DIR": str(target)})
    assert s.data_dir == str(target)
    assert not target.exists()


def test_mcp_api_is_derived_from_port_when_unset():
    assert L({"MYMEAL_PORT": "9000"}).mcp_api == "http://127.0.0.1:9000/api/v1"


def test_settings_are_immutable():
    s = L({})
    with pytest.raises(Exception):
        s.PORT = 1


# --------------------------------------------------------------- app factory

def test_two_apps_with_different_settings_in_one_process(tmp_path):
    """Previously impossible: Config captured os.environ at import time, so the
    first import decided configuration for the whole process."""
    from app import create_app

    class A:
        DATA_DIR = str(tmp_path / "a")
        DATABASE_URL = f"sqlite:///{tmp_path/'a.db'}"
        DISABLE_AUTH = True

    class B:
        DATA_DIR = str(tmp_path / "b")
        DATABASE_URL = f"sqlite:///{tmp_path/'b.db'}"
        DISABLE_AUTH = False

    app_a, app_b = create_app(A), create_app(B)
    assert app_a.config["DISABLE_AUTH"] is True
    assert app_b.config["DISABLE_AUTH"] is False
    assert app_a.config["SETTINGS"].data_dir != app_b.config["SETTINGS"].data_dir


def test_importing_settings_module_creates_nothing(tmp_path):
    """Import must have no filesystem side effects.

    Runs in a SUBPROCESS on purpose: importlib.reload() would rebind
    ConfigError to a new class object and silently break every later
    ``pytest.raises(ConfigError)`` in this module.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import app.settings"],
        cwd=str(tmp_path), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": os.path.abspath(os.path.dirname(os.path.dirname(__file__)))},
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_cors_is_same_origin_by_default(tmp_path):
    """The old factory called CORS(app, supports_credentials=True) with no
    origin list, which reflects ANY origin back — any website could make
    credentialed calls to the API."""
    from app import create_app

    class C:
        DATA_DIR = str(tmp_path / "c")
        DATABASE_URL = f"sqlite:///{tmp_path/'c.db'}"

    client = create_app(C).test_client()
    r = client.get("/api/v1/misc/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_cors_allows_only_configured_origins(tmp_path):
    from app import create_app

    class C:
        DATA_DIR = str(tmp_path / "d")
        DATABASE_URL = f"sqlite:///{tmp_path/'d.db'}"
        CORS_ORIGINS = ("http://localhost:5173",)

    client = create_app(C).test_client()
    ok = client.get("/api/v1/misc/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"
    bad = client.get("/api/v1/misc/health", headers={"Origin": "https://evil.example"})
    assert bad.headers.get("Access-Control-Allow-Origin") != "https://evil.example"


def test_proxy_headers_are_ignored_unless_a_proxy_is_declared(tmp_path):
    from app import create_app

    class C:
        DATA_DIR = str(tmp_path / "e")
        DATABASE_URL = f"sqlite:///{tmp_path/'e.db'}"

    app = create_app(C)
    from werkzeug.middleware.proxy_fix import ProxyFix
    assert not isinstance(app.wsgi_app, ProxyFix)


@pytest.mark.skipif(os.getuid() == 0,
                    reason="root bypasses filesystem permission checks, so an "
                           "unwritable directory cannot be simulated here")
def test_unwritable_data_dir_fails_at_startup_not_at_request_time(tmp_path):
    """An unwritable volume must be a clear startup error, not a confusing 500
    on the first upload."""
    from app import create_app

    blocked = tmp_path / "ro"
    blocked.mkdir()
    (blocked / "sub").mkdir()
    os.chmod(blocked, 0o500)

    class C:
        DATA_DIR = str(blocked / "sub" / "deeper")
        DATABASE_URL = f"sqlite:///{tmp_path/'f.db'}"

    try:
        with pytest.raises(RuntimeError) as exc:
            create_app(C)
        assert "MYMEAL_DATA_DIR" in str(exc.value) or "not writable" in str(exc.value)
    finally:
        os.chmod(blocked, 0o700)


def test_storage_preparation_reports_an_actionable_error(tmp_path, monkeypatch):
    """Root cannot be blocked by permissions, so assert the guard's behaviour
    directly: a failure to create the directory must name MYMEAL_DATA_DIR
    rather than surfacing a bare OSError later."""
    from app import _prepare_storage
    from app.settings import load_settings

    settings = load_settings(env={"MYMEAL_DATA_DIR": str(tmp_path / "x")},
                             ha_options={})

    def boom(*a, **kw):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(os, "makedirs", boom)
    with pytest.raises(RuntimeError) as exc:
        _prepare_storage(settings)
    msg = str(exc.value)
    assert "MYMEAL_DATA_DIR" in msg and "Permission denied" in msg


# --------------------------------------------------------------- health probes

def _mk(tmp_path, name, **attrs):
    from app import create_app

    ns = {"DATA_DIR": str(tmp_path / name),
          "DATABASE_URL": f"sqlite:///{tmp_path/(name+'.db')}"}
    ns.update(attrs)
    return create_app(type("C", (), ns)).test_client()


def test_liveness_ignores_dependencies(tmp_path):
    r = _mk(tmp_path, "live").get("/api/v1/health/live")
    assert r.status_code == 200 and r.get_json()["status"] == "alive"


def test_readiness_reports_hard_dependencies(tmp_path):
    r = _mk(tmp_path, "ready", MCP_ENABLED=False).get("/api/v1/health/ready")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["storage"] == "ok"


def test_optional_ai_provider_does_not_make_the_app_unready(tmp_path):
    """A third-party AI outage must not take the recipe manager offline."""
    r = _mk(tmp_path, "ai", MCP_ENABLED=False, AI_PROVIDER="claude").get("/api/v1/health/ready")
    assert r.status_code == 200
    assert "ai_provider" not in r.get_json()["required"]


def test_dead_mcp_is_reported_but_not_fatal_by_default(tmp_path):
    """MCP_ENABLED with no process running: visible, but the app still serves."""
    r = _mk(tmp_path, "mcp1", MCP_ENABLED=True, MCP_PORT=59999).get("/api/v1/health/ready")
    assert r.status_code == 200
    assert r.get_json()["checks"]["mcp"] == "error"


def test_dead_mcp_is_fatal_when_declared_required(tmp_path):
    """The container must not claim readiness when a REQUIRED process is dead."""
    r = _mk(tmp_path, "mcp2", MCP_ENABLED=True, MCP_REQUIRED=True,
            MCP_PORT=59999).get("/api/v1/health/ready")
    assert r.status_code == 503
    assert "mcp" in r.get_json()["notReady"]


def test_health_endpoints_expose_no_secrets(tmp_path):
    client = _mk(tmp_path, "leak", MCP_ENABLED=False, SECRET_KEY=GOOD_SECRET,
                 OPENAI_API_KEY="sk-must-not-leak")
    for path in ("/api/v1/health/live", "/api/v1/health/ready",
                 "/api/v1/misc/health", "/api/v1/status"):
        body = client.get(path).get_data(as_text=True)
        assert GOOD_SECRET not in body and "sk-must-not-leak" not in body


def test_legacy_health_endpoint_still_works(tmp_path):
    """Existing Docker health checks point at this path."""
    assert _mk(tmp_path, "legacy").get("/api/v1/misc/health").status_code == 200


def test_long_placeholder_secret_is_still_rejected():
    """Regression: the shipped docker-compose.yml said
    'please-change-me-to-a-long-random-string' — long enough to pass a length
    check, and accepted by exact-match placeholder detection."""
    with pytest.raises(ConfigError) as exc:
        L({"MYMEAL_SECRET_KEY": "please-change-me-to-a-long-random-string"})
    assert "placeholder" in str(exc.value)


@pytest.mark.parametrize("value", [
    "my-example-key-aaaaaaaaaaaaaaaaaaaaaaaa",
    "CHANGE-ME-NOW-aaaaaaaaaaaaaaaaaaaaaaaaa",
    "placeholder-secret-aaaaaaaaaaaaaaaaaaaa",
    "replace-me-with-something-aaaaaaaaaaaaa",
])
def test_placeholder_markers_are_rejected_case_insensitively(value):
    with pytest.raises(ConfigError):
        L({"MYMEAL_SECRET_KEY": value})


def test_a_real_random_secret_is_accepted():
    import secrets as _s
    assert L({"MYMEAL_SECRET_KEY": _s.token_urlsafe(32)}).SECRET_KEY


# --------------------------------------------------------------- AI providers

def test_providers_read_current_settings_not_a_process_cache(tmp_path):
    """Providers were built once from os.environ and cached process-wide, so
    the first app to use AI froze provider config for every later app."""
    from app.services.ai.registry import _instance

    class S1:
        OLLAMA_HOST = "http://one:11434"
        OLLAMA_MODEL = "m1"
        AI_TIMEOUT_SECONDS = 30

    class S2:
        OLLAMA_HOST = "http://two:11434"
        OLLAMA_MODEL = "m2"
        AI_TIMEOUT_SECONDS = 30

    assert _instance("ollama", S1()).host == "http://one:11434"
    assert _instance("ollama", S2()).host == "http://two:11434"


def test_provider_honours_the_configured_ai_timeout():
    from app.services.ai.registry import _instance

    class S:
        OLLAMA_HOST = "http://x:11434"
        OLLAMA_MODEL = "m"
        AI_TIMEOUT_SECONDS = 7

    assert _instance("ollama", S()).timeout == 7


def test_ollama_discovery_returns_none_when_nothing_answers(monkeypatch):
    """Absence must be an ordinary result, not an exception or a hang."""
    from app.services.ai import discovery

    monkeypatch.setattr(discovery, "_probe", lambda *a, **k: None)
    monkeypatch.setattr(discovery, "_supervisor_addon_hosts", lambda: [])
    assert discovery.discover_ollama() is None


def test_ollama_discovery_prefers_a_supervisor_addon(monkeypatch):
    from app.services.ai import discovery

    monkeypatch.setattr(discovery, "_supervisor_addon_hosts",
                        lambda: ["http://addon-ollama:11434"])
    monkeypatch.setattr(discovery, "_probe",
                        lambda host, **k: ["llama3.1"] if "addon" in host else None)
    found = discovery.discover_ollama()
    assert found["host"] == "http://addon-ollama:11434"
    assert found["via"] == "supervisor"
    assert "llama3.1" in found["models"]


def test_ollama_discovery_falls_back_to_probing(monkeypatch):
    from app.services.ai import discovery

    monkeypatch.setattr(discovery, "_supervisor_addon_hosts", lambda: [])
    monkeypatch.setattr(discovery, "_probe",
                        lambda host, **k: ["m"] if "localhost" in host else None)
    found = discovery.discover_ollama()
    assert found["host"] == "http://localhost:11434" and found["via"] == "probe"


def test_ollama_discovery_never_raises_on_network_errors(monkeypatch):
    from app.services.ai import discovery

    def boom(*a, **k):
        raise OSError("network unreachable")

    monkeypatch.setattr(discovery.httpx, "get", boom)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert discovery.discover_ollama() is None


def test_discovery_endpoint_requires_authentication(tmp_path):
    """It probes internal network addresses, so it must never be anonymous."""
    client = _mk(tmp_path, "disc", MCP_ENABLED=False)
    assert client.get("/api/v1/ai/discover-ollama").status_code == 401
