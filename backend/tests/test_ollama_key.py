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
