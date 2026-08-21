"""Async Voyage AI and local embedding providers."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

import httpx

from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
)

_VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"


class _AsyncHTTPClient(Protocol):
    async def post(self, url: str, **kwargs: object) -> Any: ...

    async def aclose(self) -> None: ...


class VoyageProvider:
    """Voyage embeddings over a cancellable async HTTP transport."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = "voyage-4-large",
        request_timeout_seconds: float = 30.0,
        http_client: _AsyncHTTPClient | None = None,
    ) -> None:
        key = api_key or os.getenv("VOYAGE_API_KEY", "")
        self._model = model
        self._client: Any = http_client or httpx.AsyncClient(
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(request_timeout_seconds),
        )

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
        raise NotImplementedError(
            "VoyageProvider supports embeddings only. "
            "Use AnthropicProvider or OpenAICompatibleProvider for completions."
        )

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        return EmbedResponse(
            embeddings=await self._embed_texts(request.texts, request.input_type),
            model=self._model,
        )

    async def _embed_texts(
        self,
        texts: list[str],
        input_type: str,
    ) -> list[list[float]]:
        response = await self._client.post(
            _VOYAGE_EMBEDDINGS_URL,
            json={
                "input": texts,
                "model": self._model,
                "input_type": input_type,
            },
        )
        response.raise_for_status()
        payload = response.json()
        data = sorted(payload.get("data", []), key=lambda item: int(item.get("index", 0)))
        return [list(item.get("embedding", [])) for item in data]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings: list[list[float]] = []
        for index in range(0, len(texts), 96):
            embeddings.extend(await self._embed_texts(texts[index : index + 96], "document"))
        return embeddings

    async def aclose(self) -> None:
        await self._client.aclose()

    def supports_vision(self) -> bool:
        return False

    def supports_tool_use(self) -> bool:
        return False


class LocalEmbedProvider:
    """Local sentence-transformers embedding provider."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError("Install 'sentence-transformers' to use LocalEmbedProvider") from exc
        self._model = SentenceTransformer(model_name)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise NotImplementedError(
            "LocalEmbedProvider supports embeddings only. "
            "Use AnthropicProvider or OpenAICompatibleProvider for completions."
        )

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        import asyncio

        embeddings = await asyncio.to_thread(lambda: self._model.encode(request.texts).tolist())
        return EmbedResponse(embeddings=embeddings)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        import asyncio

        if not texts:
            return []
        embeddings = await asyncio.to_thread(lambda: self._model.encode(texts).tolist())
        return cast(list[list[float]], embeddings)

    def supports_vision(self) -> bool:
        return False

    def supports_tool_use(self) -> bool:
        return False
