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
class TokenUsage:
    """Normalised token counts from any provider response.

    All providers return token counts in slightly different shapes; this
    dataclass gives the rest of the codebase a single, stable interface.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class CompletionResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: TokenUsage | None = None  # populated by providers for real cost tracking

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

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one provider call (batch API).

        Default implementation falls back to sequential ``embed()`` calls.
        Providers should override this with their native batch endpoint
        (Voyage max 96 texts; OpenAI max 2048 texts per request).

        Returns a list of embedding vectors, one per input text.
        """
        results: list[list[float]] = []
        for text in texts:
            resp = await self.embed(EmbedRequest(texts=[text]))
            results.extend(resp.embeddings)
        return results

    def supports_vision(self) -> bool: ...

    def supports_tool_use(self) -> bool: ...


# -- Standalone helpers --------------------------------------------------------


async def embed_texts(
    texts: list[str], provider: LLMProvider | None = None
) -> list[list[float]]:
    """Embed texts using the given provider, or return empty embeddings as fallback.

    Callers must handle empty embeddings (``[]``) gracefully — they indicate
    that no real embedder is configured. Random noise vectors are never returned
    as they silently corrupt RAG retrieval quality.
    """
    if provider is not None:
        try:
            resp = await provider.embed(EmbedRequest(texts=texts))
            return resp.embeddings
        except NotImplementedError:
            pass  # provider doesn't support embedding -- fall through to empty
    # No real provider configured — return empty embeddings instead of random noise.
    # Callers must handle empty embeddings gracefully.
    return [[] for _ in texts]
