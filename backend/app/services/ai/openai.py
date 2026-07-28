"""OpenAI provider (Chat Completions).

Model is configurable via ``MYMEAL_OPENAI_MODEL``. Uses the official ``openai``
SDK's ``chat.completions`` surface, which Ollama and other OpenAI-compatible
servers also speak — but a dedicated Ollama adapter ships separately so a local
install needs no OpenAI dependency.
"""
from __future__ import annotations

import json
import os

from .base import AIProvider, ChatResult, ProviderError, ToolCall


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, settings=None):
        from .settings_access import resolved
        cfg = resolved(settings)
        self.api_key = cfg.OPENAI_API_KEY or os.environ.get(
            "OPENAI_API_KEY"
        )
        self.model = cfg.OPENAI_MODEL
        self.timeout = cfg.AI_TIMEOUT_SECONDS
        self.base_url = cfg.OPENAI_BASE_URL or None  # optional override
        self._client = None

    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as exc:  # pragma: no cover - dependency missing
                raise ProviderError("openai SDK not installed") from exc
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = openai.OpenAI(**kwargs)
        return self._client

    def _complete(self, system: str, prompt: str, max_tokens: int) -> str:
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content or ""

    def _complete_image(self, system, prompt, image_b64, media_type, max_tokens) -> str:
        client = self._get_client()
        data_uri = f"data:{media_type};base64,{image_b64}"
        resp = client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ]},
            ],
        )
        return resp.choices[0].message.content or ""

    def chat(self, messages, system="", tools=None, max_tokens=2048) -> ChatResult:
        client = self._get_client()
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        kwargs = {"model": self.model, "max_tokens": max_tokens, "messages": msgs}
        if tools:
            kwargs["tools"] = [
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
        resp = client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        out = ChatResult(content=msg.content or "")
        for call in msg.tool_calls or []:
            out.tool_calls.append(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=json.loads(call.function.arguments or "{}"),
                )
            )
        return out

    def chat_stream(self, messages, system="", tools=None, max_tokens=2048):
        """True token streaming via Chat Completions ``stream=True``. Text arrives
        as ``delta.content``; tool calls arrive fragmented across chunks and are
        reassembled by index before their JSON arguments are parsed."""
        client = self._get_client()
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        kwargs = {"model": self.model, "max_tokens": max_tokens,
                  "messages": msgs, "stream": True}
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"], "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object"})}}
                for t in tools
            ]
        content = ""
        frags: dict[int, dict] = {}
        try:
            for chunk in client.chat.completions.create(**kwargs):
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    content += delta.content
                    yield {"type": "delta", "text": delta.content}
                for tc in (delta.tool_calls or []):
                    slot = frags.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments
        except Exception as exc:  # noqa: BLE001 - normalize SDK/HTTP errors
            raise ProviderError(f"openai request failed: {exc}") from exc
        out = ChatResult(content=content)
        for idx in sorted(frags):
            f = frags[idx]
            try:
                args = json.loads(f["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            out.tool_calls.append(ToolCall(
                id=f["id"] or f"call_{idx}", name=f["name"], arguments=args))
        yield {"type": "final", "result": out}
