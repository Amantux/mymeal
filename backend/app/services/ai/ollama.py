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

from .base import AIProvider, ChatResult, ProviderError, ToolCall, safe_upstream_detail


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

    def _post(self, payload: dict) -> dict:
        try:
            r = httpx.post(f"{self.host}/api/chat", json=payload,
                           headers=self._headers(), timeout=self.timeout)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"ollama request failed: {safe_upstream_detail(exc)}"
            ) from exc
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
            with httpx.stream("POST", f"{self.host}/api/chat", json=payload,
                              headers=self._headers(), timeout=self.timeout) as r:
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
            raise ProviderError(
                f"ollama request failed: {safe_upstream_detail(exc)}"
            ) from exc
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
