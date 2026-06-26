"""Voyage AI embedding provider.

Uses the voyageai SDK for best-in-class retrieval embeddings.
Falls back to a sentence-transformers local model if voyageai is not available.
"""

from __future__ import annotations

from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
)


class VoyageProvider:
    """Voyage AI embedding provider (recommended for production RAG).

    Args:
        api_key: Voyage AI API key. Reads from env VOYAGE_API_KEY if not given.
        model: Voyage embedding model (e.g. "voyage-2", "voyage-large-2").
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = "voyage-2",
    ) -> None:
        try:
            import voyageai  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "Install 'voyageai' to use VoyageProvider"
            ) from exc

        self._client = voyageai.Client(api_key=api_key)
        self._model = model

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise NotImplementedError(
            "VoyageProvider supports embeddings only. "
            "Use AnthropicProvider or OpenAICompatibleProvider for completions."
        )

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        import asyncio

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.embed(request.texts, model=self._model),
        )
        embeddings: list[list[float]] = result.embeddings
        return EmbedResponse(embeddings=embeddings)

    def supports_vision(self) -> bool:
        return False

    def supports_tool_use(self) -> bool:
        return False


class LocalEmbedProvider:
    """Local sentence-transformers embedding provider (CPU, no API key needed).

    Used as fallback in CI environments where Voyage AI is not available.

    Args:
        model_name: HuggingFace model name (e.g. "all-MiniLM-L6-v2").
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "Install 'sentence-transformers' to use LocalEmbedProvider"
            ) from exc

        from sentence_transformers import SentenceTransformer  # type: ignore[import]
        self._model = SentenceTransformer(model_name)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise NotImplementedError(
            "LocalEmbedProvider supports embeddings only. "
            "Use AnthropicProvider or OpenAICompatibleProvider for completions."
        )

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        import asyncio

        embeddings = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._model.encode(request.texts).tolist(),
        )
        return EmbedResponse(embeddings=embeddings)

    def supports_vision(self) -> bool:
        return False

    def supports_tool_use(self) -> bool:
        return False
