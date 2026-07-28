"""The /diagnostics endpoint feeds the in-app bug reporter. It must require auth
and never leak a secret (the report seeds a PUBLIC GitHub issue)."""


def test_diagnostics_requires_auth(client):
    assert client.get("/api/v1/diagnostics").status_code == 401


def test_diagnostics_reports_coarse_facts(auth_client):
    r = auth_client.get("/api/v1/diagnostics")
    assert r.status_code == 200
    body = r.get_json()
    assert body["app"] == "myMeal"
    assert body["dbBackend"] == "sqlite"
    assert isinstance(body["mcpEnabled"], bool)


def test_diagnostics_leaks_no_secrets(auth_client, monkeypatch):
    monkeypatch.setenv("MYMEAL_OPENAI_API_KEY", "sk-supersecret-value")
    monkeypatch.setenv("MYMEAL_OPENAI_BASE_URL", "http://192.168.9.9:1234/v1")
    monkeypatch.setenv("MYMEAL_DATABASE_URL", "postgresql://u:p@db/x")
    raw = auth_client.get("/api/v1/diagnostics").get_data(as_text=True)
    assert "sk-supersecret-value" not in raw
    assert "192.168.9.9" not in raw
    assert "postgresql://" not in raw
