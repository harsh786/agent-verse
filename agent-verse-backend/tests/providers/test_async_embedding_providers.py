"""Current async embedding provider request and cancellation contracts."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.providers.base import EmbedRequest


class _VoyageResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}


class _VoyageHTTPClient:
    def __init__(self, *, block: bool = False) -> None:
        self.block = block
        self.cancelled = asyncio.Event()
        self.closed = False
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, **kwargs: object) -> _VoyageResponse:
        self.calls.append((url, dict(kwargs)))
        if self.block:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return _VoyageResponse()

    async def aclose(self) -> None:
        self.closed = True


async def test_voyage_uses_async_http_current_model_and_input_type() -> None:
    from app.providers.voyage_provider import VoyageProvider

    client = _VoyageHTTPClient()
    provider = VoyageProvider(api_key="key", http_client=client, request_timeout_seconds=7.5)

    result = await provider.embed(EmbedRequest(texts=["query"], input_type="query"))

    assert result.embeddings == [[0.1, 0.2]]
    url, kwargs = client.calls[0]
    assert url == "https://api.voyageai.com/v1/embeddings"
    assert kwargs["json"] == {
        "input": ["query"],
        "model": "voyage-4-large",
        "input_type": "query",
    }


async def test_voyage_cancellation_reaches_async_network_call_and_client_closes() -> None:
    from app.providers.voyage_provider import VoyageProvider

    client = _VoyageHTTPClient(block=True)
    provider = VoyageProvider(api_key="key", http_client=client)
    task = asyncio.create_task(provider.embed(EmbedRequest(texts=["document"])))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await provider.aclose()
    assert client.cancelled.is_set()
    assert client.closed


async def test_voyage_configures_bounded_transport_timeout() -> None:
    from app.providers.voyage_provider import VoyageProvider

    provider = VoyageProvider(api_key="key", request_timeout_seconds=7.5)
    assert provider._client.timeout.read == 7.5
    await provider.aclose()


class _GeminiTypes:
    class HttpOptions:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

    class EmbedContentConfig:
        def __init__(self, *, task_type: str) -> None:
            self.task_type = task_type


class _GeminiModels:
    def __init__(self, *, block: bool = False) -> None:
        self.block = block
        self.cancelled = asyncio.Event()
        self.calls: list[dict[str, object]] = []

    async def embed_content(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if self.block:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.3, 0.4])]
        )


class _GeminiClient:
    def __init__(self, models: _GeminiModels) -> None:
        self.aio = SimpleNamespace(models=models, aclose=self._aclose)
        self.closed = False

    async def _aclose(self) -> None:
        self.closed = True


async def test_gemini_uses_aio_embed_current_model_task_and_timeout() -> None:
    from app.providers.gemini_provider import GeminiProvider

    models = _GeminiModels()
    client = _GeminiClient(models)
    genai = MagicMock()
    genai.Client.return_value = client
    genai.types = _GeminiTypes
    google = MagicMock()
    google.genai = genai

    with patch.dict(sys.modules, {"google": google, "google.genai": genai}):
        provider = GeminiProvider(api_key="key", request_timeout_ms=8_000)
        result = await provider.embed(
            EmbedRequest(texts=["query"], input_type="query")
        )

    assert result.embeddings == [[0.3, 0.4]]
    assert genai.Client.call_args.kwargs["http_options"].timeout == 8_000
    assert models.calls[0]["model"] == "gemini-embedding-001"
    assert models.calls[0]["config"].task_type == "RETRIEVAL_QUERY"


async def test_gemini_cancellation_reaches_aio_call_and_client_closes() -> None:
    from app.providers.gemini_provider import GeminiProvider

    models = _GeminiModels(block=True)
    client = _GeminiClient(models)
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._client = client
    provider._types = _GeminiTypes
    provider._embed_model = "gemini-embedding-001"
    task = asyncio.create_task(provider.embed(EmbedRequest(texts=["document"])))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await provider.aclose()
    assert models.cancelled.is_set()
    assert client.closed
