"""Ollama provider — local, private, no API key.

Talks to a local Ollama server's ``/api/chat`` endpoint over HTTP. Default host
is ``http://localhost:11434``; model via ``MYMEAL_OLLAMA_MODEL``. This is the
privacy-first option for Home Assistant users who self-host everything.
"""
from __future__ import annotations

import os

import httpx

from .base import AIProvider, ChatResult, ProviderError, ToolCall


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self):
        self.host = os.environ.get("MYMEAL_OLLAMA_HOST", "http://localhost:11434")
        self.model = os.environ.get("MYMEAL_OLLAMA_MODEL", "llama3.1")

    def available(self) -> bool:
        # A model name is always set; treat configured host as availability.
        return bool(self.host and self.model)

    def _post(self, payload: dict) -> dict:
        try:
            r = httpx.post(f"{self.host}/api/chat", json=payload, timeout=120)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama request failed: {exc}") from exc
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
