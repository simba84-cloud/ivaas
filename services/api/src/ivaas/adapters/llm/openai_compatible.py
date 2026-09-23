"""ChatModel adapter for any OpenAI-compatible /v1/chat/completions server.

Verified target: Ollama. The same protocol is served by vLLM, llama.cpp's
server and LocalAI, so moving from a laptop to a GPU inference cluster is a
URL change, not a code change.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ivaas.ports.assistant import (
    ChatMessage,
    ChatModelUnavailableError,
    ToolCall,
    ToolSpec,
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _wire(m: ChatMessage) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in m.tool_calls
        ]
    if m.tool_call_id is not None:
        out["tool_call_id"] = m.tool_call_id
    return out


def _arguments(raw: Any) -> dict[str, Any]:
    """Servers disagree on whether arguments arrive as a JSON string or an object."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class OpenAiCompatibleChatModel:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout_s
        )
        self._model = model

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check(self) -> str | None:
        """None if the model is ready, else a human-readable problem. Used at startup."""
        try:
            r = await self._client.get("/v1/models")
            r.raise_for_status()
            ids = {m.get("id") for m in r.json().get("data", [])}
        except (httpx.HTTPError, ValueError) as exc:
            return f"language model server unreachable: {type(exc).__name__}"
        if self._model not in ids and self._model.split(":")[0] not in {
            i.split(":")[0] for i in ids
        }:
            have = sorted(ids) or "none"
            return (
                f"model '{self._model}' is not installed (have: {have}); "
                f"run: ollama pull {self._model}"
            )
        return None

    async def complete(self, messages: list[ChatMessage], tools: list[ToolSpec]) -> ChatMessage:
        body = {
            "model": self._model,
            "messages": [_wire(m) for m in messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ],
            "temperature": 0.1,  # analysis, not creative writing
            "stream": False,
        }
        try:
            r = await self._client.post("/v1/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise ChatModelUnavailableError(
                f"language model server is unreachable ({type(exc).__name__})"
            ) from exc
        if r.status_code == 404:
            raise ChatModelUnavailableError(
                f"model '{self._model}' is not installed on the language model server; "
                f"run: ollama pull {self._model}"
            )
        if r.status_code >= 400:
            raise ChatModelUnavailableError(
                f"language model server returned {r.status_code}: {r.text[:200]}"
            )
        try:
            message = r.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ChatModelUnavailableError("language model returned an unexpected reply") from exc

        calls = tuple(
            ToolCall(
                id=c.get("id") or f"call_{i}",
                name=c.get("function", {}).get("name", ""),
                arguments=_arguments(c.get("function", {}).get("arguments")),
            )
            for i, c in enumerate(message.get("tool_calls") or [])
        )
        content = _THINK.sub("", message.get("content") or "").strip()
        return ChatMessage("assistant", content, tool_calls=calls)
