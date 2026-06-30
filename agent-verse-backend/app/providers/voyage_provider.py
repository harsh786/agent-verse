"""Voyage AI embedding provider.

Uses the voyageai SDK for best-in-class retrieval embeddings.
Falls back to a sentence-transformers local model if voyageai is not available.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

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

    async def stream_tokens(
        self,
        request: CompletionRequest,
        on_token: Callable[[str], Awaitable[None]],
    ) -> CompletionResponse:
        """Not supported — VoyageProvider is embedding-only."""
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

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed up to 96 texts in one Voyage API call (Voyage batch limit).

        Splits into batches of 96 automatically when *texts* is longer.
        """
        import asyncio

        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        batch_size = 96  # Voyage AI API limit per request

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda b=batch: self._client.embed(b, model=self._model),
            )
            all_embeddings.extend(result.embeddings)

        return all_embeddings

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

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts using the local sentence-transformers model."""
        import asyncio

        if not texts:
            return []

        embeddings = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._model.encode(texts).tolist(),
        )
        return embeddings

    def supports_vision(self) -> bool:
        return False

    def supports_tool_use(self) -> bool:
        return False
