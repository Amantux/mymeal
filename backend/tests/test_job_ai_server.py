"""Background jobs can run on their own SLM server.

The point: a fast hosted model for interactive chat, and a small local model on
your own box for the slow async work. That needs a per-area BASE URL, not just a
provider — two Ollama servers are the common case and both would otherwise
resolve the single shared ollama_base_url.
"""
import pytest

from app.services.ai.provider_config import job_override


def test_an_unset_area_still_means_same_as_chat(noauth_app, gid):
    """The default must not change: no async override = use the chat provider."""
    from app.services.ai.registry import resolve_job_provider

    app = noauth_app
    app.test_client().get("/api/v1/ai/job-settings")
    with app.app_context():
        assert resolve_job_provider("nutrition", gid) is None


def test_a_job_can_point_at_its_own_server(noauth_app, gid):
    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/job-settings", json={
        "enrich": {"provider": "ollama", "model": "qwen3:4b",
                   "baseUrl": "http://192.168.1.50:11434"}})
    with app.app_context():
        from app.services.ai.registry import resolve_job_provider

        p = resolve_job_provider("nutrition", gid)
        assert p.host == "http://192.168.1.50:11434"
        assert p.model == "qwen3:4b"


def test_the_async_server_does_not_leak_into_chat(noauth_app, gid):
    """The whole feature is that these are separate. If the async host bled into
    the chat provider it would silently move interactive traffic onto the slow
    box."""
    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/settings", json={"provider": "ollama",
                                       "baseUrl": "http://fast-box:11434"})
    c.put("/api/v1/ai/job-settings", json={
        "enrich": {"provider": "ollama", "baseUrl": "http://slow-box:11434"}})
    with app.app_context():
        from app.services.ai.registry import provider_for_group, resolve_job_provider

        assert provider_for_group(gid).host == "http://fast-box:11434"
        assert resolve_job_provider("nutrition", gid).host == "http://slow-box:11434"


def test_the_two_job_areas_are_independent(noauth_app, gid):
    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/job-settings", json={
        "enrich": {"provider": "ollama", "baseUrl": "http://box-a:11434"},
        "organize": {"provider": "ollama", "baseUrl": "http://box-b:11434"}})
    with app.app_context():
        from app.services.ai.registry import resolve_job_provider

        assert resolve_job_provider("nutrition", gid).host == "http://box-a:11434"
        assert resolve_job_provider("categorize", gid).host == "http://box-b:11434"


def test_a_per_run_option_beats_the_stored_server(noauth_app, gid):
    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/job-settings", json={
        "enrich": {"provider": "ollama", "baseUrl": "http://stored:11434"}})
    with app.app_context():
        from app.services.ai.registry import resolve_job_provider

        p = resolve_job_provider("nutrition", gid,
                                 opts={"baseUrl": "http://per-run:11434"})
        assert p.host == "http://per-run:11434"


# --- secrets ----------------------------------------------------------------

def test_the_async_api_key_is_never_returned(noauth_app):
    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/job-settings", json={
        "enrich": {"provider": "ollama", "apiKey": "sk-async-secret"}})

    body = c.get("/api/v1/ai/job-settings").get_json()

    assert body["enrich"]["apiKeySet"] is True
    assert "sk-async-secret" not in str(body)
    assert "apiKey" not in body["enrich"]


def test_a_blank_apikey_on_resave_keeps_the_stored_one(noauth_app, gid):
    """The form never receives the key back, so blank must mean 'unchanged' —
    otherwise every unrelated save wipes it."""
    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/job-settings", json={"enrich": {"apiKey": "sk-keep-me"}})
    # apiKey="" explicitly — what a form sends when the field is left empty.
    # Omitting the field instead never exercised the rule at all.
    c.put("/api/v1/ai/job-settings", json={"enrich": {"model": "other", "apiKey": ""}})

    with app.app_context():
        assert job_override(gid, "nutrition")["api_key"] == "sk-keep-me"


def test_clearing_the_async_key_is_explicit(noauth_app, gid):
    app = noauth_app
    c = app.test_client()
    c.put("/api/v1/ai/job-settings", json={"enrich": {"apiKey": "sk-gone"}})
    c.put("/api/v1/ai/job-settings", json={"enrich": {"clearApiKey": True}})

    with app.app_context():
        assert job_override(gid, "nutrition")["api_key"] is None


# --- the URL guard ----------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "http://169.254.169.254",          # cloud metadata
    "file:///etc/passwd",
    "not-a-url",
])
def test_an_unsafe_async_server_is_refused_on_save(noauth_app, bad):
    r = noauth_app.test_client().put(
        "/api/v1/ai/job-settings", json={"enrich": {"baseUrl": bad}})
    assert r.status_code == 422


def test_an_unsafe_server_supplied_per_run_is_refused_at_use(noauth_app, gid):
    """Per-run opts never pass the settings guard, so the check has to exist at
    the point of USE too."""
    from app.services.ai.base import ProviderError
    from app.services.ai.registry import resolve_job_provider

    app = noauth_app
    app.test_client().get("/api/v1/ai/job-settings")
    with app.app_context():
        with pytest.raises(ProviderError) as ei:
            resolve_job_provider("nutrition", gid,
                                 opts={"provider": "ollama",
                                       "baseUrl": "http://169.254.169.254"})
    assert "not allowed" in str(ei.value)


def test_a_private_lan_server_is_allowed(noauth_app):
    """Self-hosting on the LAN is the entire use case — it must not be blocked."""
    r = noauth_app.test_client().put(
        "/api/v1/ai/job-settings",
        json={"enrich": {"provider": "ollama", "baseUrl": "http://192.168.1.50:11434"}})
    assert r.status_code == 200
    assert r.get_json()["enrich"]["baseUrl"] == "http://192.168.1.50:11434"
