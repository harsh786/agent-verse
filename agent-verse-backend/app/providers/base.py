"""LLMProvider protocol and shared request/response types.

All provider implementations must satisfy this structural protocol.
No inheritance required — duck-typing via Protocol.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
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
    response_schema: dict[str, Any] | None = None  # JSON Schema; when set, provider MUST
    # return content that is a single valid
    # JSON object matching it
    json_object: bool = False  # request plain JSON-object mode (no schema): the model
    # must return a single JSON object. Weaker than response_schema (no shape
    # enforcement) but reliably suppresses prose / chain-of-thought preambles.
    tool_choice: str | None = None  # override tool-call policy when tools are present:
    # "required" forces a tool call, "auto" lets the model choose, "none" forbids.
    # None keeps each provider's default (force a call when tools are offered).
    cache_prefix: str | None = None  # stable prefix for Anthropic ephemeral caching


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
    # Thinking (reasoning) models — reported by providers that can tell:
    reasoning_tokens: int = 0  # completion tokens this response spent reasoning
    thinking_observed: bool = False  # reasoning seen on any attempt of this call
    thinking_disabled: bool = False  # this answer was produced with thinking turned off

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# -- Embedding -----------------------------------------------------------------


@dataclass
class EmbedRequest:
    texts: list[str]
    model: str = ""
    input_type: Literal["query", "document"] = "document"


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

    async def stream_tokens(
        self,
        request: CompletionRequest,
        on_token: Callable[[str], Awaitable[None]],
    ) -> CompletionResponse:
        """Stream tokens one-by-one, calling on_token for each chunk.

        Default implementation: calls complete() then calls on_token once with
        the full response text.  Providers that support real streaming override
        this method for true token-level delivery.
        """
        response = await self.complete(request)
        if response.content:
            await on_token(response.content)
        return response

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

    def supports_structured_output(self) -> bool:
        """Return True if this provider supports structured JSON output."""
        return False


# -- Standalone helpers --------------------------------------------------------


class EmbedderUnavailableError(RuntimeError):
    """No embedder is configured, or the configured one cannot embed (PROV-08)."""


async def embed_texts(texts: list[str], provider: LLMProvider | None = None) -> list[list[float]]:
    """Embed texts with *provider*.

    Raises :class:`EmbedderUnavailableError` when there is no embedder or it does
    not support embeddings. It used to return ``[]`` vectors, and callers stored
    vectorless chunks that silently fell back to lexical search. Random noise
    vectors are never returned either.
    """
    if provider is None:
        raise EmbedderUnavailableError("no embedding provider is configured")
    try:
        resp = await provider.embed(EmbedRequest(texts=texts))
    except NotImplementedError as exc:
        raise EmbedderUnavailableError(
            f"the configured provider cannot embed: {exc or type(provider).__name__}"
        ) from exc
    return resp.embeddings
