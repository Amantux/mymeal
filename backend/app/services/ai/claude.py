"""Anthropic Claude provider.

Uses the official ``anthropic`` SDK. Model IDs and call shape were confirmed
against the claude-api reference (not memory): the default model is
``claude-opus-4-8`` and requests go through ``client.messages.create`` with
``system`` as a top-level parameter.
"""
from __future__ import annotations

import os

from .base import AIProvider, ChatResult, ProviderError, ToolCall, safe_upstream_detail


class ClaudeProvider(AIProvider):
    name = "claude"

    def __init__(self, settings=None):
        from .settings_access import resolved
        cfg = resolved(settings)
        self.api_key = cfg.ANTHROPIC_API_KEY or os.environ.get(
            "ANTHROPIC_API_KEY"
        )
        self.model = cfg.CLAUDE_MODEL
        self.timeout = cfg.AI_TIMEOUT_SECONDS
        self._client = None

    def available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency missing
                raise ProviderError("anthropic SDK not installed") from exc
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def _complete(self, system: str, prompt: str, max_tokens: int) -> str:
        client = self._get_client()
        # Raw SDK errors must not escape: callers (recipe import) only handle
        # ProviderError, so an unwrapped one becomes a 500 with an HTML body and
        # unsanitized upstream text.
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:   # normalise every SDK failure
            raise ProviderError(
                f"claude request failed: {safe_upstream_detail(exc)}"
            ) from exc
        return "".join(b.text for b in resp.content if b.type == "text")

    def _complete_image(self, system, prompt, image_b64, media_type, max_tokens) -> str:
        client = self._get_client()
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": media_type,
                                                 "data": image_b64}},
                    {"type": "text", "text": prompt},
                ]}],
            )
        except Exception as exc:   # normalise every SDK failure
            raise ProviderError(
                f"claude request failed: {safe_upstream_detail(exc)}"
            ) from exc
        return "".join(b.text for b in resp.content if b.type == "text")

    def chat(self, messages, system="", tools=None, max_tokens=2048) -> ChatResult:
        client = self._get_client()
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {"type": "object"}),
                }
                for t in tools
            ]
        try:
            resp = client.messages.create(**kwargs)
        except Exception as exc:   # normalise every SDK failure
            raise ProviderError(
                f"claude request failed: {safe_upstream_detail(exc)}"
            ) from exc
        out = ChatResult()
        for block in resp.content:
            if block.type == "text":
                out.content += block.text
            elif block.type == "tool_use":
                out.tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
        return out

    def chat_stream(self, messages, system="", tools=None, max_tokens=2048):
        """True token streaming via the Anthropic SDK's ``messages.stream``: text
        deltas come from ``text_stream``; any ``tool_use`` blocks (with their
        accumulated input) are read from the final message once the stream ends."""
        client = self._get_client()
        kwargs = {"model": self.model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {"name": t["name"], "description": t.get("description", ""),
                 "input_schema": t.get("parameters", {"type": "object"})}
                for t in tools
            ]
        content = ""
        try:
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    content += text
                    yield {"type": "delta", "text": text}
                final = stream.get_final_message()
        except Exception as exc:
            raise ProviderError(
                f"claude request failed: {safe_upstream_detail(exc)}"
            ) from exc
        out = ChatResult(content=content)
        for block in final.content:
            if block.type == "tool_use":
                out.tool_calls.append(ToolCall(
                    id=block.id, name=block.name, arguments=dict(block.input)))
        yield {"type": "final", "result": out}
