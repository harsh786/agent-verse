"""LLMProvider protocol and shared request/response types.

All provider implementations must satisfy this structural protocol.
No inheritance required — duck-typing via Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

# -- Message types -------------------------------------------------------------

MessageRole = Literal["system", "user", "assistant", "tool"]


class Message(BaseModel):
    """Chat message -- uses Pydantic for runtime role validation at API boundaries."""

    role: MessageRole
    content: str | list[dict[str, Any]]
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    image_data: str | None = None  # Base64-encoded image for vision models


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


# -- Completion ----------------------------------------------------------------

@dataclass
class CompletionRequest:
    messages: list[Message]
    model: str
    system: str | None = None
    tools: list[ToolDefinition] = field(default_factory=list)
    max_tokens: int = 4096
    temperature: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# -- Embedding -----------------------------------------------------------------

@dataclass
class EmbedRequest:
    texts: list[str]
    model: str = ""
    input_type: str = "document"


@dataclass
class EmbedResponse:
    embeddings: list[list[float]]
    model: str = ""
    total_tokens: int = 0


# -- Provider protocol ---------------------------------------------------------

@runtime_checkable
class LLMProvider(Protocol):
    """Structural protocol every provider must satisfy.

    Using Protocol (not ABC) so third-party wrappers need not inherit our class.
    """

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    async def embed(self, request: EmbedRequest) -> EmbedResponse: ...

    def supports_vision(self) -> bool: ...

    def supports_tool_use(self) -> bool: ...


# -- Standalone helpers --------------------------------------------------------


async def embed_texts(
    texts: list[str], provider: LLMProvider | None = None
) -> list[list[float]]:
    """Embed texts using the given provider, or return random unit-norm vectors as fallback."""
    import math
    import random

    if provider is not None:
        try:
            resp = await provider.embed(EmbedRequest(texts=texts))
            return resp.embeddings
        except NotImplementedError:
            pass  # provider doesn't support embedding -- fall through to random
    # Fallback: random unit-norm vectors (768-dim)
    results = []
    for _ in texts:
        raw = [random.gauss(0, 1) for _ in range(768)]
        mag = math.sqrt(sum(x * x for x in raw))
        results.append([x / mag for x in raw] if mag > 0 else raw)
    return results
