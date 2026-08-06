"""Baseline hardening headers.

myMeal was the only one of the three apps shipping none. Ported from HomeHoard.
The HA-specific subtlety is the one worth testing: under ingress the app is
legitimately framed by Home Assistant, so asserting anti-clickjacking
unconditionally would blank the panel inside HA.
"""
import pytest


def test_baseline_headers_are_present(client):
    r = client.get("/api/v1/status")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert "Content-Security-Policy" in r.headers


@pytest.mark.parametrize("directive", [
    "default-src 'self'",
    "script-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])
def test_the_csp_locks_down_the_dangerous_directives(client, directive):
    csp = client.get("/api/v1/status").headers["Content-Security-Policy"]
    assert directive in csp


def test_only_the_two_video_embed_hosts_are_framable(client):
    """services/videos.py will only ever produce an embed URL for these two, and
    every other link opens in a new tab rather than being framed."""
    csp = client.get("/api/v1/status").headers["Content-Security-Policy"]
    frame_src = [p for p in csp.split(";") if p.strip().startswith("frame-src")][0]
    assert "https://www.youtube-nocookie.com" in frame_src
    assert "https://player.vimeo.com" in frame_src
    assert "*" not in frame_src


def test_standalone_asserts_anti_clickjacking(client):
    """Auth enabled = running standalone, so nothing should frame us."""
    r = client.get("/api/v1/status")
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in r.headers["Content-Security-Policy"]


def test_behind_ingress_we_do_not_block_framing(noauth_app):
    """DISABLE_AUTH is how the add-on runs behind HA ingress. Home Assistant
    frames the panel legitimately — asserting frame-ancestors here would show
    the user a blank panel."""
    r = noauth_app.test_client().get("/api/v1/status")
    assert "X-Frame-Options" not in r.headers
    assert "frame-ancestors" not in r.headers["Content-Security-Policy"]


def test_hsts_is_not_sent_over_plain_http(client):
    """Sending HSTS over http:// is meaningless, and pinning it from a LAN
    address would be actively unhelpful."""
    assert "Strict-Transport-Security" not in client.get("/api/v1/status").headers
