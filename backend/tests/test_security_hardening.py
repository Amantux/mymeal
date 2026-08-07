"""Security hardening: SSRF at the point of use, safe SPA path joins, safe SQL
identifier composition, and credential-free upstream error text.

Each test pins a specific fix from the code-scanning triage. They assert
BEHAVIOUR (a request is not made, a traversal is refused, a secret is redacted)
rather than the shape of the code, so a refactor that keeps the guarantee keeps
the tests green.
"""

import pytest
from app.services.ai.base import ProviderError, safe_upstream_detail
from app.services.ai.provider_config import list_models

# --- SSRF: validate the base URL where it is USED, not only where it is saved --

class _Eff:
    """An effective-settings stand-in that returns "" for any AI_* field a
    provider reads but the test didn't set — so a provider constructor can't
    AttributeError. Explicit values still win."""
    def __init__(self, provider, **kw):
        self.__dict__["AI_PROVIDER"] = provider
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return ""


def _eff(provider, **kw):
    return _Eff(provider, **kw)


def _no_network(monkeypatch):
    """Record whether an HTTP client is ever constructed.

    Returns a dict the test asserts on POSITIVELY. Raising here would be
    swallowed by list_models' `except Exception: return []`, making the test
    pass even with the guard removed — so the guarantee is 'no client was
    built', not 'the call returned []'.
    """
    import httpx

    seen = {"opened": False}

    def spy(*a, **k):
        seen["opened"] = True
        raise AssertionError("network client opened for a blocked URL")

    monkeypatch.setattr(httpx, "Client", spy)
    return seen


def test_link_local_ollama_host_is_refused_without_a_request(monkeypatch):
    # 169.254.169.254 is the cloud metadata endpoint. An operator-supplied value
    # from env/add-on options never passes the /ai/settings guard, so the block
    # has to happen here.
    seen = _no_network(monkeypatch)
    eff = _eff("ollama", OLLAMA_HOST="http://169.254.169.254")
    assert list_models(eff) == []
    assert seen["opened"] is False   # blocked BEFORE any client was constructed


def test_link_local_openai_base_url_is_refused_without_a_request(monkeypatch):
    seen = _no_network(monkeypatch)
    eff = _eff("openai", OPENAI_BASE_URL="http://169.254.169.254/v1")
    assert list_models(eff) == []
    assert seen["opened"] is False


def test_private_lan_ollama_host_still_reaches_the_provider(monkeypatch):
    """The guard must NOT break a self-hosted Ollama on the LAN/loopback."""
    called = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "llama3"}, {"name": "mistral"}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            called["url"] = url
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)

    eff = _eff("ollama", OLLAMA_HOST="http://192.168.1.50:11434")
    assert list_models(eff) == ["llama3", "mistral"]
    assert called["url"] == "http://192.168.1.50:11434/api/tags"


def test_get_provider_refuses_a_link_local_ollama_host(monkeypatch):
    """list_models was guarded; the CHAT path (get_provider) was not, so an
    env/add-on-options host reached the network with the API key attached.
    Validate at the point of use on every path.
    """
    from app.services.ai.base import ProviderError
    from app.services.ai.registry import get_provider

    eff = _eff("ollama", OLLAMA_HOST="http://169.254.169.254",
               OLLAMA_MODEL="llama3", OLLAMA_API_KEY="k")
    with pytest.raises(ProviderError):
        get_provider(eff)


def test_get_provider_refuses_a_link_local_openai_base_url(monkeypatch):
    from app.services.ai.base import ProviderError
    from app.services.ai.registry import get_provider

    eff = _eff("openai", OPENAI_BASE_URL="http://169.254.169.254/v1",
               OPENAI_MODEL="gpt-4", OPENAI_API_KEY="k")
    with pytest.raises(ProviderError):
        get_provider(eff)


def test_get_provider_allows_a_private_lan_host(monkeypatch):
    """The guard must not break a self-hosted LAN Ollama on the chat path."""
    from app.services.ai.registry import get_provider

    eff = _eff("ollama", OLLAMA_HOST="http://192.168.1.50:11434",
               OLLAMA_MODEL="llama3", OLLAMA_API_KEY="k")
    provider = get_provider(eff)          # must not raise
    assert provider is not None


# --- SPA path traversal ------------------------------------------------------

def test_spa_serves_a_nested_asset(app, tmp_path):
    """safe_join must still allow ordinary nested asset paths."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)")
    (dist / "index.html").write_text("<html></html>")
    app.config["SETTINGS"].values["FRONTEND_DIST"] = str(dist)

    r = app.test_client().get("/assets/app-abc123.js")
    assert r.status_code == 200
    assert b"console.log(1)" in r.data


def test_spa_refuses_traversal_without_erroring(app, tmp_path):
    """A traversal must not 500 and must not serve a file outside the dist dir —
    it falls through to the SPA index instead.

    Driven through _serve_spa directly, not the URL router: Flask normalises
    "/../x" before routing, which would make an HTTP-level test vacuous. The
    old os.path.join built a real path outside dist and os.path.isfile returned
    True for it — that probe is the defect being fixed here.
    """
    from app import _serve_spa

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>spa</html>")
    (tmp_path / "secret.txt").write_text("root:x:0:0:")
    app.config["SETTINGS"].values["FRONTEND_DIST"] = str(dist)

    with app.test_request_context("/"):
        body = _serve_spa("../secret.txt")
        if isinstance(body, tuple):          # the "frontend not built" notice
            rendered = body[0].encode()
        else:                                # a file response (the SPA index)
            body.direct_passthrough = False
            rendered = body.get_data()
    assert b"root:" not in rendered
    assert b"spa" in rendered                # fell through to index.html


# --- Upstream error text carries no credentials ------------------------------

@pytest.mark.parametrize("raw, secret", [
    ("401 unauthorized for key sk-abcdef1234567890", "sk-abcdef1234567890"),
    ("request failed: Bearer abcdef1234567890xyz", "abcdef1234567890xyz"),
    ("connect to https://user:hunter2@api.example.com/v1 failed", "hunter2"),
    ('{"error":{"message":"bad","api_key":"sk-livekey123456"}}', "sk-livekey123456"),
])
def test_safe_upstream_detail_redacts_credentials(raw, secret):
    out = safe_upstream_detail(RuntimeError(raw))
    assert secret not in out
    assert "[redacted]" in out


def test_safe_upstream_detail_keeps_a_useful_summary():
    out = safe_upstream_detail(ConnectionRefusedError("connection refused"))
    assert "ConnectionRefusedError" in out      # the type still aids debugging
    assert "connection refused" in out


def test_safe_upstream_detail_truncates_a_long_response_body():
    # Realistic upstream body: prose, so no single token trips the high-entropy
    # redaction — this pins the TRUNCATION guarantee specifically.
    body = "upstream returned an unexpected error while processing the request. " * 80
    out = safe_upstream_detail(RuntimeError(body))
    assert len(out) < 300
    assert out.endswith("…")


def test_safe_upstream_detail_redacts_a_long_opaque_blob():
    # A long unbroken alphanumeric run is what a leaked key/token looks like,
    # so it is redacted outright rather than truncated (which would leave a
    # usable prefix).
    out = safe_upstream_detail(RuntimeError("x" * 5000))
    assert "xxxx" not in out
    assert "[redacted]" in out


def test_provider_error_message_is_still_actionable():
    """Sanitising must not genericise the curated messages users rely on."""
    exc = ProviderError("this AI provider does not support image input")
    assert "does not support image input" in str(exc)


# --- anti-clickjacking is keyed on ingress, not on auth mode -----------------

def test_frame_headers_asserted_for_a_non_ingress_request(app):
    """A standalone/browser request must get X-Frame-Options + frame-ancestors,
    so the app cannot be framed by an attacker."""
    client = app.test_client()
    resp = client.get("/api/v1/misc/health")
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in resp.headers.get("Content-Security-Policy", "")


def test_frame_headers_omitted_for_an_ingress_request(app):
    """From the ingress peer, HA legitimately frames the app — asserting
    anti-clickjacking blanks the panel. This must hold even with auth ENABLED
    (disable_auth: false behind ingress is a supported configuration, because
    ingress identity is honoured regardless of auth mode).
    """
    client = app.test_client()
    resp = client.get("/api/v1/misc/health",
                      environ_overrides={"REMOTE_ADDR": "172.30.32.2"})
    assert "X-Frame-Options" not in resp.headers
    assert "frame-ancestors" not in resp.headers.get("Content-Security-Policy", "")


# --- image path is safe_join'd, like the video path -------------------------

def test_image_path_refuses_traversal(app):
    from werkzeug.exceptions import NotFound
    from app.api.recipes import _image_path
    with app.test_request_context():
        # A crafted filename that would escape the images dir must 404, not
        # resolve to /etc/passwd. secure_filename + a UUID name make this
        # unreachable via the API today; the guard is defence-in-depth.
        try:
            path = _image_path("../../../../etc/passwd")
        except NotFound:
            return  # safe_join rejected it → abort(404)
        assert "etc/passwd" not in path
