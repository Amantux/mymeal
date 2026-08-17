"""SSRF guard for a user-supplied LLM base URL / Ollama host.

The provider base URL is operator-configurable and the DB-override path bypasses
the env-layer URL parser, so a malicious value could point myMeal's server at an
internal service. Block link-local (notably the cloud metadata endpoint
169.254.169.254 / fe80::) while still allowing loopback and private LAN, where a
self-hosted Ollama / SLM server legitimately runs.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx


def llm_url_ok(url: str) -> tuple[bool, str | None]:
    """Return (ok, error). Blank URL → ok (falls back to the configured default).

    Checks the resolved address now; not hardened against DNS-rebinding on its
    own — callers that actually issue the request should use
    ``llm_pinned_get_args`` below, which resolves once and connects to that
    exact address.
    """
    if not url:
        return True, None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "base URL must be http or https"
    host = parsed.hostname
    if not host:
        return False, "base URL has no host"
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        # Unresolvable / malformed host: let the real request fail rather than 500.
        return True, None
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        # An IPv4-mapped IPv6 address (::ffff:169.254.169.254) connects to the real
        # IPv4 on a dual-stack host, so test the mapped v4, not the v6 wrapper.
        if addr.version == 6 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        if addr.is_link_local:
            return False, "base URL host is not allowed"
    return True, None


def is_ollama_cloud_host(url: str) -> bool:
    """True when ``url``'s HOST is exactly ollama.com (or a subdomain of it).

    A proper hostname comparison, not a substring match — ``"http://evil.com/
    ollama.com"`` or ``"http://ollama.com.evil.com"`` must not match just
    because the text "ollama.com" appears somewhere in the URL.
    """
    host = (urlparse(url or "").hostname or "").lower()
    return host == "ollama.com" or host.endswith(".ollama.com")


class UnsafeHostError(ValueError):
    """Raised by ``llm_pinned_get_args`` when the resolved host is not allowed."""


def llm_pinned_get_args(url: str) -> tuple[str, dict, dict]:
    """Resolve + validate ``url``'s host (same policy as ``llm_url_ok``: loopback
    and private LAN allowed, link-local blocked) and return ``(pinned_url,
    headers, extensions)`` for an httpx GET that connects to that EXACT resolved
    address — closing the DNS-rebinding TOCTOU window where a hostname resolves
    to an allowed address here but a different (blocked) one when the HTTP
    client re-resolves it at connect time.

    Raises ``UnsafeHostError`` if the host cannot be resolved or resolves to a
    disallowed address. Mirrors ``recipe_import.pinned_get_args``, which applies
    the same pin-to-resolved-IP technique under a stricter (public-only) policy
    for fetching third-party recipe pages; this variant keeps loopback/private
    LAN allowed because a self-hosted Ollama legitimately runs there.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise UnsafeHostError("URL has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError) as exc:
        raise UnsafeHostError(f"could not resolve host: {exc}") from exc
    pinned_ip = ""
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.version == 6 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        if addr.is_link_local:
            raise UnsafeHostError("host is not allowed")
        if not pinned_ip:
            pinned_ip = info[4][0]
    if not pinned_ip:
        raise UnsafeHostError("could not resolve host")

    u = httpx.URL(url)
    pinned = str(u.copy_with(host=pinned_ip))
    try:
        host_is_ip = bool(ipaddress.ip_address(u.host))
    except ValueError:
        host_is_ip = False
    # Bracket an IPv6 literal in the Host header; don't send an IP as SNI (SNI
    # is for hostnames — httpcore falls back to the pinned IP for an IP source).
    host_header = (f"[{u.host}]" if ":" in u.host else u.host) + (f":{u.port}" if u.port else "")
    extensions = {"sni_hostname": u.host} if u.scheme == "https" and not host_is_ip else {}
    return pinned, {"Host": host_header}, extensions
