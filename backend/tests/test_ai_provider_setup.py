"""Provider-setup hardening + status: the base URL is SSRF-guarded (link-local /
cloud-metadata blocked, LAN/loopback allowed) and /ai/status reports whether a
usable provider is configured (for the chat widget's setup state)."""


def test_ai_settings_rejects_link_local_base_url(auth_client):
    # 169.254.169.254 is the cloud-metadata endpoint — must be refused.
    r = auth_client.put("/api/v1/ai/settings",
                        json={"provider": "ollama", "baseUrl": "http://169.254.169.254"})
    assert r.status_code == 422
    assert "not allowed" in r.get_json()["error"]


def test_ai_settings_allows_lan_ollama_host(auth_client):
    r = auth_client.put("/api/v1/ai/settings",
                        json={"provider": "ollama", "baseUrl": "http://192.168.1.50:11434"})
    assert r.status_code == 200


def test_ai_settings_rejects_non_http_scheme(auth_client):
    r = auth_client.put("/api/v1/ai/settings",
                        json={"provider": "ollama", "baseUrl": "file:///etc/passwd"})
    assert r.status_code == 422


def test_ai_models_probe_rejects_link_local(auth_client):
    r = auth_client.post("/api/v1/ai/models",
                         json={"provider": "ollama", "baseUrl": "http://169.254.169.254"})
    assert r.status_code == 422


def test_ai_status_reports_unconfigured_by_default(auth_client):
    body = auth_client.get("/api/v1/ai/status").get_json()
    assert body["enabled"] is False   # no provider configured in the test env
    assert "provider" in body


def test_ai_status_requires_auth(client):
    assert client.get("/api/v1/ai/status").status_code == 401
