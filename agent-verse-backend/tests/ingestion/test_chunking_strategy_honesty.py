"""KB-13: an unimplemented chunking strategy is refused, never silently aliased.

``parent_child``, ``sentence_window``, ``fixed``, ``agentic`` and
``agentic_chunking`` all mapped to ``SemanticChunker`` (and any unknown name
did too), so a tenant choosing a strategy got semantic chunks while the Source
reported the strategy it asked for.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ingestion.chunkers import (
    SUPPORTED_CHUNKING_STRATEGIES,
    UnsupportedChunkingStrategyError,
    get_chunker_for_strategy,
)
from app.ingestion.chunkers.semantic import SemanticChunker
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-kb13", plan=PlanTier.ENTERPRISE, api_key_id="k")
_KEY = "av_kb13"


@pytest.mark.parametrize(
    "name", ["parent_child", "sentence_window", "agentic", "agentic_chunking", "made_up"]
)
def test_unimplemented_strategies_are_refused(name: str) -> None:
    assert name not in SUPPORTED_CHUNKING_STRATEGIES
    with pytest.raises(UnsupportedChunkingStrategyError):
        get_chunker_for_strategy(name)


def test_fixed_is_a_real_fixed_size_chunker() -> None:
    chunker = get_chunker_for_strategy("fixed")
    assert not isinstance(chunker, SemanticChunker)
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = chunker.chunk(text)
    assert len(chunks) >= 3
    assert all(len(c.content.split()) <= 400 for c in chunks)


def _client() -> TestClient:
    from app.api.ingestion import router

    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _create(client: TestClient, strategy: str) -> Any:
    return client.post(
        "/sources",
        json={
            "name": "docs",
            "family": "web",
            "source_type": "http",
            "chunking_strategy": strategy,
        },
        headers={"X-API-Key": _KEY},
    )


@pytest.mark.parametrize("strategy", ["sentence_window", "parent_child", "agentic"])
def test_creating_a_source_with_an_unimplemented_strategy_is_422(strategy: str) -> None:
    resp = _create(_client(), strategy)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("strategy", ["auto", "heading", "fixed"])
def test_creating_a_source_with_a_supported_strategy_works(strategy: str) -> None:
    resp = _create(_client(), strategy)
    assert resp.status_code == 201, resp.text
    assert resp.json()["chunking_strategy"] == strategy


def test_updating_a_source_to_an_unimplemented_strategy_is_422() -> None:
    client = _client()
    source_id = _create(client, "auto").json()["source_id"]
    resp = client.patch(
        f"/sources/{source_id}",
        json={"chunking_strategy": "sentence_window"},
        headers={"X-API-Key": _KEY},
    )
    assert resp.status_code == 422, resp.text


async def test_a_stored_config_with_an_unsupported_strategy_fails_the_document() -> None:
    class _Store:
        chunks: list[Any] = []

        async def exists_by_hash(self, **_: Any) -> bool:
            return False

        async def ingest_chunks_async(self, chunks: list[Any], **_: Any) -> list[str]:
            self.chunks.extend(chunks)
            return [c.chunk_id for c in chunks]

    class _Embedder:
        async def embed(self, request: Any) -> Any:
            from app.providers.base import EmbedResponse

            return EmbedResponse(embeddings=[[0.1] * 8 for _ in request.texts], model="m")

    store = _Store()
    pipeline = IngestionPipeline(knowledge_store=store, embedder=_Embedder())
    doc = RawDocument(
        doc_id="d",
        source_id="s",
        tenant_id=_CTX.tenant_id,
        content=("A sentence about retention policy. " * 30).encode(),
        content_type="text/plain",
    )
    config = SourceConfig(
        source_id="s",
        tenant_id=_CTX.tenant_id,
        name="n",
        family=SourceFamily.WEB,
        source_type="http",
        collection_id="c",
        chunking_strategy="sentence_window",
        min_quality_score=0.0,
    )

    result = await pipeline.ingest(doc, config)

    assert result.status == "failed"
    assert "sentence_window" in (result.error or "")
    assert store.chunks == []
