"""Ollama's hosted web search + fetch (ollama.com) — used to import a recipe by
name (search the web, then import the best hit).

  POST https://ollama.com/api/web_search {query, max_results}
       -> {"results": [{"title", "url", "content"}, ...]}
  POST https://ollama.com/api/web_fetch  {url}
       -> {"title", "content", "links"}

Authenticated with OLLAMA_SEARCH_KEY (falling back to the OLLAMA_API_KEY used by
the Ollama Cloud provider). Best-effort and bounded — never raises to the caller.
"""
import logging

import httpx

from .ai.settings_access import resolved

_LOGGER = logging.getLogger("mymeal.websearch")
_SEARCH_URL = "https://ollama.com/api/web_search"
_FETCH_URL = "https://ollama.com/api/web_fetch"
_TIMEOUT = 20.0


def _key(settings=None) -> str:
    cfg = resolved(settings)
    return (getattr(cfg, "OLLAMA_SEARCH_KEY", "")
            or getattr(cfg, "OLLAMA_API_KEY", "") or "").strip()


def enabled(settings=None) -> bool:
    return bool(_key(settings))


def web_search(query, *, max_results=3, settings=None):
    """Hosted web search → a list of {title, url, content}, or [] on any failure."""
    key = _key(settings)
    if not key or not (query or "").strip():
        return []
    try:
        r = httpx.post(_SEARCH_URL, headers={"Authorization": f"Bearer {key}"},
                       json={"query": query, "max_results": max_results}, timeout=_TIMEOUT)
        r.raise_for_status()
        return (r.json() or {}).get("results") or []
    except Exception as exc:  # noqa: BLE001 - absence/network is the normal failure
        _LOGGER.info("web_search failed: %s", exc)
        return []


def web_fetch(url, *, settings=None):
    """Hosted fetch of a page's cleaned content → {title, content, links} or None."""
    key = _key(settings)
    if not key or not (url or "").strip():
        return None
    try:
        r = httpx.post(_FETCH_URL, headers={"Authorization": f"Bearer {key}"},
                       json={"url": url}, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json() or None
    except Exception as exc:  # noqa: BLE001 - best-effort
        _LOGGER.info("web_fetch failed: %s", exc)
        return None
