"""Ollama provider — local and private by default.

Talks to an Ollama server's ``/api/chat`` endpoint over HTTP. Default host is
``http://localhost:11434``; model via ``MYMEAL_OLLAMA_MODEL``. This is the
privacy-first option for Home Assistant users who self-host everything. A plain
local server needs no key; set ``MYMEAL_OLLAMA_API_KEY`` to send a bearer token
for Ollama Cloud or a secured/proxied instance.
"""
from __future__ import annotations

import json

import httpx

import logging

from ...logsafe import scrub
from .base import AIProvider, ChatResult, ProviderError, ToolCall, safe_upstream_detail
from .url_guard import UnsafeHostError, is_ollama_cloud_host, llm_pinned_get_args

_LOGGER = logging.getLogger("mymeal")


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, settings=None):
        # Settings are resolved once at startup and passed in, rather than each
        # provider re-reading os.environ at first use — which cached whatever
        # the environment happened to be when the process first needed AI.
        from .settings_access import resolved
        cfg = resolved(settings)
        self.host = cfg.OLLAMA_HOST
        self.model = cfg.OLLAMA_MODEL
        self.timeout = cfg.AI_TIMEOUT_SECONDS
        self.api_key = getattr(cfg, "OLLAMA_API_KEY", "") or ""
        self._discovered = None

    def available(self) -> bool:
        # A model name is always set; treat configured host as availability.
        return bool(self.host and self.model)

    def _headers(self) -> dict:
        # Ollama Cloud / a secured instance accepts a bearer token; a plain local
        # server ignores it. Only send when configured.
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _where(self) -> str:
        """A human label for the configured host, safe to put in a client-facing
        message OR a log line. ``self.host`` is operator-set (env/add-on option
        or a DB override that bypasses the env-layer parser) and could contain a
        literal newline crafted to forge a fake log entry or split a returned
        error message — scrub it before it reaches either sink."""
        if is_ollama_cloud_host(self.host or ""):
            return "Ollama Cloud"
        return f"Ollama at {scrub(self.host or '')}"

    def _explain(self, exc: httpx.HTTPError) -> str:
        """Turn an httpx failure into something the operator can act on.

        The default rendering is actively misleading: httpx's HTTPStatusError
        stringifies as "Client error '401 Unauthorized' for url
        'https://ollama.com/api/chat' For more information check <MDN link>",
        so the most prominent thing in the message is the URL — which is never
        the problem. It sent at least one person looking for a wrong endpoint
        when their key was simply missing.

        Curated messages per status; the upstream response body is deliberately
        NOT echoed. Anything unrecognised still falls back to the redacted
        summary so an unexpected failure is not swallowed.
        """
        where = self._where()

        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code in (401, 403):
                return (f"{where} rejected the API key. Add or correct the Ollama "
                        f"API key in Settings → AI provider.")
            if code == 404:
                # /api/chat exists on both local and cloud (a genuinely missing
                # path returns 'path not found'), so a 404 here means the MODEL
                # is not available, not that the endpoint is wrong.
                return (f"{where} does not have the model “{self.model}”. Pick one "
                        f"from the model list in Settings — a cloud key cannot use "
                        f"a model you only have locally, and vice versa.")
            if code == 429:
                return f"{where} is rate-limiting this key. Wait a moment and retry."
            if code >= 500:
                return f"{where} had a server error ({code}). This is upstream; retry shortly."
            return f"{where} refused the request ({code})."

        if isinstance(exc, httpx.TimeoutException):
            return (f"{where} did not respond within {self.timeout:g}s. A large model on "
                    f"modest hardware can exceed this — raise the AI timeout in Settings.")
        if isinstance(exc, httpx.ConnectError):
            return (f"Could not reach {where}. Check the host is correct and the "
                    f"server is running.")
        # Unrecognised failure: the redacted detail is still exception-derived
        # (CWE-209), so it is logged server-side only; the client gets a
        # message built from data we control (where + the exception's class).
        # `where` is already scrub()'d by _where() above.
        _LOGGER.warning("%s request failed: %s", where, scrub(safe_upstream_detail(exc)))
        return (f"{where} request failed unexpectedly ({type(exc).__name__}). "
                "Check the server logs for details.")

    def _post(self, payload: dict) -> dict:
        try:
            # /api/chat, not /api/generate: this provider needs the messages
            # array and tool calling, which /api/generate (single `prompt`,
            # no tools) does not support. Both endpoints exist on local and
            # cloud; only this one can drive the assistant.
            #
            # Pinned to the resolved IP (not just f"{self.host}/api/chat"):
            # get_provider() already validated self.host via llm_url_ok at
            # construction time, but a second, separate DNS resolution here —
            # what a plain httpx.post(f"{self.host}/...") triggers — could
            # return a different (blocked) address. Connecting to the exact
            # address already checked closes that TOCTOU window.
            pinned, host_hdr, ext = llm_pinned_get_args(f"{self.host}/api/chat")
            r = httpx.post(pinned, json=payload,
                           headers={**self._headers(), **host_hdr}, extensions=ext,
                           timeout=self.timeout)
            r.raise_for_status()
        except UnsafeHostError as exc:
            raise ProviderError(f"{self._where()} is not allowed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(self._explain(exc)) from exc
        return r.json()

    def _complete(self, system: str, prompt: str, max_tokens: int) -> str:
        data = self._post(
            {
                "model": self.model,
                "stream": False,
                "format": "json",
                "options": {"num_predict": max_tokens},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            }
        )
        return (data.get("message") or {}).get("content", "")

    def _complete_image(self, system, prompt, image_b64, media_type, max_tokens) -> str:
        # Ollama takes images as base64 strings on the message. Needs a
        # vision-capable model (llava, llama3.2-vision, …); errors otherwise.
        data = self._post({
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt, "images": [image_b64]},
            ],
        })
        return (data.get("message") or {}).get("content", "")

    def chat(self, messages, system="", tools=None, max_tokens=2048) -> ChatResult:
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"num_predict": max_tokens},
            "messages": msgs,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {"type": "object"}),
                    },
                }
                for t in tools
            ]
        data = self._post(payload)
        msg = data.get("message") or {}
        out = ChatResult(content=msg.get("content", ""))
        for i, call in enumerate(msg.get("tool_calls") or []):
            fn = call.get("function", {})
            out.tool_calls.append(
                ToolCall(
                    id=f"call_{i}",
                    name=fn.get("name", ""),
                    arguments=fn.get("arguments", {}) or {},
                )
            )
        return out

    def chat_stream(self, messages, system="", tools=None, max_tokens=2048):
        """True token streaming via Ollama's NDJSON stream (one JSON object per
        line, each with an incremental ``message.content``; tool calls arrive in
        the message object, typically on the final chunk)."""
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        payload = {
            "model": self.model,
            "stream": True,
            "options": {"num_predict": max_tokens},
            "messages": msgs,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"], "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object"})}}
                for t in tools
            ]
        content = ""
        raw_calls: list[dict] = []
        try:
            # Pinned for the same reason as _post: closes the DNS-rebinding
            # TOCTOU window between the host being validated and a later,
            # separate resolution at connect time.
            pinned, host_hdr, ext = llm_pinned_get_args(f"{self.host}/api/chat")
        except UnsafeHostError as exc:
            raise ProviderError(f"{self._where()} is not allowed: {exc}") from exc
        try:
            with httpx.stream("POST", pinned, json=payload,
                              headers={**self._headers(), **host_hdr}, extensions=ext,
                              timeout=self.timeout) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    obj = json.loads(line)
                    msg = obj.get("message") or {}
                    piece = msg.get("content") or ""
                    if piece:
                        content += piece
                        yield {"type": "delta", "text": piece}
                    for call in msg.get("tool_calls") or []:
                        raw_calls.append(call)
                    if obj.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise ProviderError(self._explain(exc)) from exc
        out = ChatResult(content=content)
        for i, call in enumerate(raw_calls):
            fn = call.get("function", {})
            out.tool_calls.append(ToolCall(
                id=f"call_{i}", name=fn.get("name", ""),
                arguments=fn.get("arguments", {}) or {}))
        yield {"type": "final", "result": out}


class OllamaCloudProvider(OllamaProvider):
    """Ollama's hosted cloud (https://ollama.com). Same wire protocol as a local
    Ollama server (/api/chat, /api/tags, bearer auth) — it just always talks to
    the cloud host and REQUIRES an API key. Host/model/key are resolved from the
    ``ollama_cloud_*`` settings namespace by ``effective_settings``."""

    name = "ollama_cloud"

    def available(self) -> bool:
        # Unlike a plain local server, the cloud needs a key.
        return bool(self.host and self.model and self.api_key)
