"""Supervisor discovery payload — what Home Assistant learns about this add-on.

The MCP endpoint is advertised here so HA's MCP Client doesn't need the URL
typed by hand. These drive the payload builders directly (no Supervisor, no
network), which is the whole reason they're separate from `main()`.
"""
import ha_discovery


class _Settings:
    """Stand-in for app.settings.Settings (only `.values` is read here)."""

    def __init__(self, **values):
        self.values = values


def _on(**extra):
    return _Settings(MCP_ENABLED=True, MCP_PORT=7851, **extra)


# --- mcp_config -------------------------------------------------------------

def test_mcp_details_published_when_enabled():
    cfg = ha_discovery.mcp_config("local-mymeal", _on())
    assert cfg["mcp_port"] == 7851
    assert cfg["mcp_url"] == "http://local-mymeal:7851/sse"


def test_mcp_url_uses_the_same_host_as_the_rest_endpoint():
    """A container-id hostname is unreachable from HA Core; both must use the
    Supervisor-assigned add-on hostname."""
    payload = ha_discovery.build_payload(
        "abc-addon", "tok", ha_discovery.mcp_config("abc-addon", _on()))
    assert payload["config"]["mcp_url"].startswith("http://abc-addon:")


def test_no_mcp_keys_when_mcp_is_disabled():
    """Publishing a dead URL is worse than publishing none."""
    assert ha_discovery.mcp_config("local-mymeal", _Settings(MCP_ENABLED=False)) == {}


def test_server_token_published_only_when_set():
    assert "mcp_token" not in ha_discovery.mcp_config("h", _on(MCP_SERVER_TOKEN=""))
    assert ha_discovery.mcp_config(
        "h", _on(MCP_SERVER_TOKEN="s3cret"))["mcp_token"] == "s3cret"


def test_unreadable_settings_degrade_to_no_mcp_keys(monkeypatch):
    """Discovery is best-effort: a settings failure drops the MCP half, not startup."""
    import app.settings

    def boom(*args, **kwargs):
        raise RuntimeError("settings exploded")

    monkeypatch.setattr(app.settings, "load_settings", boom)
    assert ha_discovery.mcp_config("local-mymeal") == {}


# --- build_payload ----------------------------------------------------------

def test_existing_keys_are_unchanged():
    """The companion integration reads host/port/token — MCP is additive only."""
    config = ha_discovery.build_payload("local-mymeal", "tok", {"mcp_port": 7851})["config"]
    assert config["host"] == "local-mymeal"
    assert config["port"] == ha_discovery.PORT
    assert config["token"] == "tok"
    assert config["mcp_port"] == 7851


def test_payload_service_name():
    assert ha_discovery.build_payload("h", "t")["service"] == "mymeal"


def test_payload_without_mcp_has_only_the_rest_keys():
    assert set(ha_discovery.build_payload("h", "t")["config"]) == {"host", "port", "token"}
