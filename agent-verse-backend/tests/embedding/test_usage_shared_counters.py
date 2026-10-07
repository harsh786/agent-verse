"""KB-27: embedding usage is counted per tenant in Redis for every embed path, and
the orchestrator never falls back to a fake 10-dim model.

``/embeddings/usage`` counted only POST /embeddings calls on the serving replica
since restart; ingestion and query embeds were never counted. The non-production
orchestrator fallback still selected ``fake-embedding`` (dim 10).
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.embeddings import router as embeddings_router
from app.embedding import usage
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-kb27", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Embedder:
    _embed_model = "nv-embed"

    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1] * 8 for _ in request.texts], model="nv-embed")


class _Store:
    async def exists_by_hash(self, **_: Any) -> bool:
        return False

    async def ingest_chunks_async(self, chunks: list[Any], **_: Any) -> list[str]:
        return [c.chunk_id for c in chunks]


@pytest.fixture
def shared_redis() -> Any:
    redis = fakeredis.aioredis.FakeRedis()
    usage.configure_usage_redis(redis)
    yield redis
    usage.configure_usage_redis(None)


async def test_an_ingestion_embed_is_visible_from_another_app_instance(shared_redis: Any) -> None:
    # "Worker" process: a connector document is ingested.
    pipeline = IngestionPipeline(knowledge_store=_Store(), embedder=_Embedder())
    doc = RawDocument(
        doc_id="d",
        source_id="s",
        tenant_id=_CTX.tenant_id,
        content=("Quarterly retention review notes for the support team. " * 5).encode(),
        content_type="text/plain",
    )
    config = SourceConfig(
        source_id="s",
        tenant_id=_CTX.tenant_id,
        name="n",
        family=SourceFamily.WEB,
        source_type="http",
        collection_id="c",
        min_quality_score=0.0,
    )
    result = await pipeline.ingest(doc, config)
    assert result.status == "indexed", result

    # "API replica" (fresh router state): the usage endpoint reads the shared counters.
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return _CTX if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(embeddings_router)
    resp = TestClient(app).get("/embeddings/usage", headers={"X-API-Key": "k"})
    assert resp.status_code == 200, resp.text
    assert sum(resp.json()["usage_by_model"].values()) > 0


def test_orchestrator_never_selects_a_fake_embedding_model() -> None:
    from app.embedding.orchestrator import EmbeddingOrchestrator
    from app.ingestion.content_classifier import ContentType

    class _EmptyRegistry:
        def __getattr__(self, name: str) -> Any:
            return lambda *a, **k: []

    orchestrator = EmbeddingOrchestrator.__new__(EmbeddingOrchestrator)
    orchestrator._registry = _EmptyRegistry()  # type: ignore[attr-defined]
    selection = orchestrator.select(ContentType.TEXT)
    assert selection.uses_default_embedder
    assert "fake" not in selection.model_id
