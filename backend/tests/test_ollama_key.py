"""Optional Ollama bearer key (mirrors Edibl): a local server needs none, but a
configured key is sent as `Authorization: Bearer` for Ollama Cloud / a secured
instance."""
from types import SimpleNamespace

from app.services.ai.ollama import OllamaProvider


def _provider(api_key):
    cfg = SimpleNamespace(OLLAMA_HOST="http://localhost:11434", OLLAMA_MODEL="llama3.1",
                          AI_TIMEOUT_SECONDS=30, OLLAMA_API_KEY=api_key)
    return OllamaProvider(settings=cfg)


def test_no_key_sends_no_auth_header():
    assert _provider("")._headers() == {}


def test_key_sends_bearer_header():
    assert _provider("sk-secret")._headers() == {"Authorization": "Bearer sk-secret"}


def test_effective_settings_maps_ollama_key():
    from app.services.ai.provider_config import effective_settings
    from app.settings import load_settings

    base = load_settings(
        env={}, ha_options={}, strict_secret=False,
        overrides={"AI_PROVIDER": "ollama", "OLLAMA_API_KEY": "from-env",
                   "SECRET_KEY": "x" * 40},
    )
    eff = effective_settings(base, gid=None)  # no DB overrides → env value flows through
    assert eff.OLLAMA_API_KEY == "from-env"


# --- The request actually goes out --------------------------------------------
#
# _post and chat_stream passed `extensions=` to httpx's MODULE-LEVEL post()/
# stream(), which don't take it (only Client methods do) — so every Ollama call
# raised TypeError before reaching the network. It stayed invisible because the
# surrounding `except httpx.HTTPError` cannot catch a TypeError: it surfaced as
# an unhandled 500 rather than a provider error. These tests drive the real
# httpx signatures through a mock transport, so a regression fails here.

def _mounted(monkeypatch, handler):
    """Point the provider at a MockTransport while keeping the real httpx API.

    Patching httpx.Client (not the provider) is deliberate: the call has to go
    through the genuine signature, which is the thing that was broken.
    """
    import httpx
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    # The host must resolve to something allowed; localhost already does.
    return _provider("")


def test_post_reaches_the_transport_with_the_pin_intact(monkeypatch):
    import httpx
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["ext"] = request.extensions
        seen["host_header"] = request.headers.get("Host")
        return httpx.Response(200, json={"message": {"content": "hi"}})

    provider = _mounted(monkeypatch, handler)
    out = provider._post({"model": "llama3.1", "messages": []})

    assert out == {"message": {"content": "hi"}}
    # The connect-to-this-exact-IP pin must survive the move to a Client, or the
    # DNS-rebinding window this code exists to close is reopened. For plain HTTP
    # the pin IS the rewritten URL plus the original Host header; `extensions`
    # only carries SNI, and only for https (see the https test below).
    assert "127.0.0.1" in seen["url"] or "[::1]" in seen["url"]
    assert seen["host_header"] == "localhost:11434"


def test_sni_is_passed_through_for_an_https_host(monkeypatch):
    """The `extensions` kwarg is the whole reason a Client is required — this is
    the case where dropping it would silently change TLS behaviour."""
    import httpx
    from types import SimpleNamespace
    seen = {}

    def handler(request):
        seen["ext"] = request.extensions
        return httpx.Response(200, json={"message": {"content": "ok"}})

    _mounted(monkeypatch, handler)
    cfg = SimpleNamespace(OLLAMA_HOST="https://localhost:11434", OLLAMA_MODEL="m",
                          AI_TIMEOUT_SECONDS=30, OLLAMA_API_KEY="")
    OllamaProvider(settings=cfg)._post({"model": "m", "messages": []})

    assert seen["ext"].get("sni_hostname") == "localhost"


def test_chat_stream_reaches_the_transport(monkeypatch):
    import httpx
    body = (b'{"message":{"content":"He"}}\n'
            b'{"message":{"content":"llo"}}\n'
            b'{"done":true}\n')

    def handler(request):
        return httpx.Response(200, content=body)

    provider = _mounted(monkeypatch, handler)
    events = list(provider.chat_stream([{"role": "user", "content": "hi"}]))

    assert "".join(e["text"] for e in events if e.get("type") == "delta") == "Hello"


def test_an_upstream_failure_is_still_a_provider_error_not_a_crash(monkeypatch):
    import httpx
    from app.services.ai.base import ProviderError

    def handler(request):
        return httpx.Response(500, text="upstream exploded")

    provider = _mounted(monkeypatch, handler)
    try:
        provider._post({"model": "llama3.1", "messages": []})
    except ProviderError as exc:
        # And the upstream body is not echoed back to the caller (CWE-209).
        assert "upstream exploded" not in str(exc)
    else:
        raise AssertionError("a 500 from Ollama should raise ProviderError")
