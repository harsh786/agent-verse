"""Voyage async HTTP provider and local embedding coverage."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.providers.base import CompletionRequest, EmbedRequest, Message


class _Response:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self._embeddings = embeddings

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "data": [
                {"index": index, "embedding": embedding}
                for index, embedding in enumerate(self._embeddings)
            ]
        }


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.closed = False

    async def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        texts = kwargs["json"]["input"]  # type: ignore[index]
        return _Response([[float(index)] for index, _ in enumerate(texts)])

    async def aclose(self) -> None:
        self.closed = True


async def test_voyage_embed_and_batch_preserve_modes() -> None:
    from app.providers.voyage_provider import VoyageProvider

    client = _Client()
    provider = VoyageProvider(http_client=client)
    query = await provider.embed(EmbedRequest(texts=["q"], input_type="query"))
    batch = await provider.embed_batch([str(index) for index in range(100)])

    assert query.embeddings == [[0.0]]
    assert len(batch) == 100
    assert client.calls[0]["json"]["input_type"] == "query"  # type: ignore[index]
    assert all(
        call["json"]["input_type"] == "document"  # type: ignore[index]
        for call in client.calls[1:]
    )


async def test_voyage_completion_and_stream_are_unsupported() -> None:
    from app.providers.voyage_provider import VoyageProvider

    provider = VoyageProvider(http_client=_Client())
    request = CompletionRequest(
        messages=[Message(role="user", content="hello")], model="voyage-4-large"
    )
    with pytest.raises(NotImplementedError):
        await provider.complete(request)
    with pytest.raises(NotImplementedError):
        await provider.stream_tokens(request, MagicMock())


async def test_voyage_close_closes_transport() -> None:
    from app.providers.voyage_provider import VoyageProvider

    client = _Client()
    provider = VoyageProvider(http_client=client)
    await provider.aclose()
    assert client.closed


async def test_local_embed_provider_uses_sentence_transformer() -> None:
    from app.providers.voyage_provider import LocalEmbedProvider

    model = MagicMock()
    model.encode.return_value = SimpleNamespace(tolist=lambda: [[0.1], [0.2]])
    module = MagicMock()
    module.SentenceTransformer.return_value = model
    with patch.dict("sys.modules", {"sentence_transformers": module}):
        provider = LocalEmbedProvider()
        result = await provider.embed(EmbedRequest(texts=["a", "b"]))
    assert result.embeddings == [[0.1], [0.2]]
