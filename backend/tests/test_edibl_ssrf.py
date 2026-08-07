"""The Edibl connected-app URL is validated at the point of use.

It arrives from a DB Setting the operator edits; a value set via env/options
never passes the API's save-time scheme check. A link-local target (the cloud
metadata endpoint) must be refused BEFORE the stored bearer token is attached,
while a private-LAN sibling add-on stays reachable.
"""
from app.services.edibl import EdiblClient


def test_link_local_edibl_url_is_refused_before_the_request(monkeypatch):
    import httpx
    opened = {"n": 0}

    def spy(*a, **k):
        opened["n"] += 1
        raise AssertionError("request issued to a blocked URL with the token")

    monkeypatch.setattr(httpx, "get", spy)
    monkeypatch.setattr(httpx, "post", spy)

    client = EdiblClient(base_url="http://169.254.169.254", token="secrettoken")
    res = client._get("/have")

    assert res["ok"] is False and res["reachable"] is False
    assert opened["n"] == 0   # blocked before any token-bearing request


def test_private_lan_edibl_url_still_reaches_the_sibling(monkeypatch):
    import httpx
    called = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"items": []}

    def fake_get(url, **k):
        called["url"] = url
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)

    client = EdiblClient(base_url="http://192.168.1.9:8099", token="t")
    res = client._get("/have")

    assert res["ok"] is True
    assert called["url"].startswith("http://192.168.1.9:8099")
