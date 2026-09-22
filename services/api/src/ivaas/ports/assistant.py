"""Port for the language model behind the analysis assistant.

The assistant never depends on a vendor: any server speaking the OpenAI-style
chat-completions protocol satisfies this (Ollama, vLLM, llama.cpp, LocalAI for
open-weights models on your own hardware), and a hosted model could be swapped
in later by writing one more adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_call_id: str | None = None


class ChatModel(Protocol):
    async def complete(self, messages: list[ChatMessage], tools: list[ToolSpec]) -> ChatMessage: ...


class ChatModelUnavailableError(RuntimeError):
    pass
