"""KB-40: orchestrated ingestion embeddings are batched, charged and metered.

The IngestionOrchestrator's plain path (collection documents, Notion, Drive,
email) sent one uncharged, unrecorded embedding request per document.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.embedding import usage
from app.embedding.metering import (
    EmbeddingBudgetExceededError,
    EmbeddingBudgetUnverifiableError,
    embed_metered,
)
from app.embedding.orchestrator import EmbeddingOrchestrator
from app.governance.cost import CostController
from app.ingestion.content_classifier import ContentType
from app.providers.base import EmbedResponse
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext("t-meter", PlanTier.PROFESSIONAL, "k")


class _Provider:
    provider_name = "fake"

    def __init__(self) -> None:
        self.batches: list[int] = []

    async def embed(self, request: Any) -> EmbedResponse:
        self.batches.append(len(request.texts))
        return EmbedResponse(embeddings=[[0.1] * 4 for _ in request.texts], model="m")


class _FakeRedis:
    def __init__(self) -> None:
        self.h: dict[str, dict[str, int]] = {}

    async def hincrby(self, key: str, field: str, n: int) -> None:
        self.h.setdefault(key, {})
        self.h[key][field] = self.h[key].get(field, 0) + n

    async def expire(self, *_a: Any) -> None:
        return None


@pytest.fixture
def usage_redis() -> Any:
    fake = _FakeRedis()
    usage.configure_usage_redis(fake)
    yield fake
    usage.configure_usage_redis(None)


async def test_refused_charge_raises_429_error_and_embeds_nothing() -> None:
    broke = CostController(per_goal_usd=10.0, per_tenant_daily_usd=0.0)
    calls = AsyncMock(return_value=[[0.1]])
    with pytest.raises(EmbeddingBudgetExceededError):
        await embed_metered(["a"], calls, tenant_ctx=_CTX, model="m", controller=broke)
    calls.assert_not_awaited()


async def test_unverifiable_budget_fails_closed() -> None:
    controller = MagicMock()
    controller.check_and_record = AsyncMock(side_effect=ConnectionError("redis down"))
    calls = AsyncMock(return_value=[[0.1]])
    with pytest.raises(EmbeddingBudgetUnverifiableError):
        await embed_metered(["a"], calls, tenant_ctx=_CTX, model="m", controller=controller)
    calls.assert_not_awaited()


async def test_two_hundred_chunks_are_four_charged_metered_batches(usage_redis: Any) -> None:
    controller = CostController(per_goal_usd=10.0, per_tenant_daily_usd=10.0)
    provider = _Provider()

    async def _batch(batch: list[str]) -> list[list[float]]:
        return (await provider.embed(MagicMock(texts=batch))).embeddings

    vectors = await embed_metered(
        [f"word {i}" for i in range(200)],
        _batch,
        tenant_ctx=_CTX,
        model="m",
        controller=controller,
    )
    assert len(vectors) == 200
    assert provider.batches == [64, 64, 64, 8]
    assert controller.daily_total(tenant_ctx=_CTX) == pytest.approx(4 * 0.0001)
    assert usage_redis.h[f"emb:usage:{_CTX.tenant_id}"]["m"] == 400  # 2 words x 200


async def test_orchestrator_embed_for_content_is_charged(usage_redis: Any) -> None:
    provider = _Provider()
    controller = CostController(per_goal_usd=10.0, per_tenant_daily_usd=10.0)
    result = await EmbeddingOrchestrator().embed_for_content(
        [f"t {i}" for i in range(130)],
        content_type=ContentType.TEXT,
        tenant_ctx=_CTX,
        default_provider=provider,
        cost_controller=controller,
    )
    assert len(result.embeddings) == 130
    assert provider.batches == [64, 64, 2]
    assert controller.daily_total(tenant_ctx=_CTX) > 0

    broke = CostController(per_goal_usd=10.0, per_tenant_daily_usd=0.0)
    refused = _Provider()
    with pytest.raises(EmbeddingBudgetExceededError):
        await EmbeddingOrchestrator().embed_for_content(
            ["x"],
            content_type=ContentType.TEXT,
            tenant_ctx=_CTX,
            default_provider=refused,
            cost_controller=broke,
        )
    assert refused.batches == []


def test_orchestrated_collection_ingest_refused_budget_is_429() -> None:
    from fastapi.testclient import TestClient

    from app.rag.store import KnowledgeStore
    from tests.api.test_knowledge_extra4 import H, _create_collection, _make_app

    provider = _Provider()
    app = _make_app(knowledge_store=KnowledgeStore(), embedder=provider)
    app.state.cost_controller = CostController(per_goal_usd=10.0, per_tenant_daily_usd=0.0)
    client = TestClient(app, raise_server_exceptions=False)
    cid = _create_collection(client)
    resp = client.post(
        f"/knowledge/collections/{cid}/documents",
        json={"content": "Some knowledge about retention. " * 20, "in_memory_only": True},
        headers=H,
    )
    assert resp.status_code == 429, resp.text
    assert provider.batches == []
