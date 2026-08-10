"""Per-group async-job AI preference: a provider+model default for background jobs,
separate from chat. Nutrition→enrich preference; categorize/cluster→organize.
Precedence: per-run opts > stored kind preference > the chat provider."""

from app.services.ai import provider_config as pc
from app.services.ai.registry import resolve_job_provider


def test_job_preference_unset_is_none(auth_client, app, gid):
    """Every field blank means "same as chat" — the default must not change now
    that the override also carries a base_url and key."""

    with app.app_context():
        for kind in ("nutrition", "categorize"):
            assert pc.job_override(gid, kind) == {
                "provider": None, "model": None, "base_url": None, "api_key": None}


def test_organize_shared_nutrition_separate(auth_client, app, gid):

    with app.app_context():
        pc.set_job_prefs(gid, {"organize": {"provider": "ollama", "model": "llama3.1"}})
        for kind in ("categorize", "cluster"):
            got = pc.job_override(gid, kind)
            assert (got["provider"], got["model"]) == ("ollama", "llama3.1")
        assert pc.job_override(gid, "nutrition")["provider"] is None


def test_resolve_none_without_pref_or_opts(auth_client, app, gid):

    with app.app_context():
        assert resolve_job_provider("nutrition", gid, opts={}) is None


def test_resolve_switches_provider_via_preference(auth_client, app, gid):

    with app.app_context():
        # Group's chat provider is Claude, but the Nutrition job prefers a local Ollama.
        pc.set_overrides(gid, provider="ollama", base_url="http://ollama.local:11434", model="base")
        pc.set_job_prefs(gid, {"enrich": {"provider": "ollama", "model": "tinyllama"}})
        pc.set_overrides(gid, provider="claude", api_key="sk-claude")
        p = resolve_job_provider("nutrition", gid, opts={})
        assert p is not None and p.name == "ollama" and p.model == "tinyllama"


def test_per_run_opts_win_over_preference(auth_client, app, gid):

    with app.app_context():
        pc.set_overrides(gid, provider="ollama", base_url="http://o", model="base")
        pc.set_job_prefs(gid, {"enrich": {"provider": "ollama", "model": "pref-model"}})
        p = resolve_job_provider("nutrition", gid, opts={"model": "override-model"})
        assert p.model == "override-model"


def test_job_settings_endpoint_roundtrip_and_validation(auth_client):
    r = auth_client.put("/api/v1/ai/job-settings", json={
        "enrich": {"provider": "ollama", "model": "m1"}, "organize": {}})
    assert r.status_code == 200
    assert r.get_json()["enrich"] == {"provider": "ollama", "model": "m1",
                                      "baseUrl": "", "apiKeySet": False}
    assert auth_client.get("/api/v1/ai/job-settings").get_json()["enrich"]["provider"] == "ollama"
    assert auth_client.put("/api/v1/ai/job-settings",
                           json={"enrich": {"provider": "bogus"}}).status_code == 422


def test_job_settings_requires_auth(client):
    assert client.get("/api/v1/ai/job-settings").status_code == 401
