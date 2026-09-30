"""KB-08: direct uploads embed in batches, charge the tenant and honour the quota.

``/knowledge/ingest/file`` sent every chunk of an upload in one EmbedRequest (a
large file exceeded provider limits), charged nothing, and skipped the
ingestion document quota the connector pipeline enforces.
"""

from __future__ import annotations

import io
import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import knowledge as knowledge_api
from app.governance.cost import CostController
from app.ingestion.quota import IngestionQuotaExceededError
from app.providers.base import EmbedResponse
from tests.api.test_knowledge_extra4 import _CTX, H, _create_collection, _make_app


class _CountingEmbedder:
    def __init__(self) -> None:
        self.batches: list[int] = []

    async def embed(self, request: Any) -> EmbedResponse:
        self.batches.append(len(request.texts))
        return EmbedResponse(embeddings=[[0.5] * 8 for _ in request.texts], model="m")


def _request(**state: Any) -> Any:
    request = MagicMock()
    request.app.state = MagicMock(spec=[])
    for key, value in state.items():
        setattr(request.app.state, key, value)
    request.state.tenant = _CTX
    return request


async def test_a_thousand_chunks_are_embedded_in_bounded_batches() -> None:
    embedder = _CountingEmbedder()
    texts = [f"chunk {i}" for i in range(1000)]

    vectors = await knowledge_api._embed_texts_or_http(texts, embedder)

    batch = knowledge_api._EMBED_BATCH_SIZE
    assert len(embedder.batches) == math.ceil(1000 / batch)
    assert max(embedder.batches) <= batch
    assert len(vectors) == 1000


async def test_each_embedding_batch_is_charged_and_a_spent_budget_is_429() -> None:
    controller = CostController(per_goal_usd=10.0, per_tenant_daily_usd=10.0)
    embedder = _CountingEmbedder()
    request = _request(cost_controller=controller)

    await knowledge_api._embed_texts_or_http(["a"] * 130, embedder, request=request)
    assert controller.daily_total(tenant_ctx=_CTX) > 0

    broke = CostController(per_goal_usd=10.0, per_tenant_daily_usd=0.0)
    embedder2 = _CountingEmbedder()
    with pytest.raises(HTTPException) as exc:
        await knowledge_api._embed_texts_or_http(
            ["a"] * 10, embedder2, request=_request(cost_controller=broke)
        )
    assert exc.value.status_code == 429
    assert embedder2.batches == []


def _upload(client: TestClient, collection_id: str) -> Any:
    body = ("Paragraph about retention policy. " * 40).encode()
    return client.post(
        "/knowledge/ingest/file",
        files={"file": ("notes.txt", io.BytesIO(body), "text/plain")},
        data={"collection_id": collection_id},
        headers=H,
    )


def test_upload_past_the_document_quota_is_429_and_embeds_nothing() -> None:
    embedder = _CountingEmbedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    cid = _create_collection(client)
    quota = MagicMock()
    quota.check_doc_quota = AsyncMock(
        side_effect=IngestionQuotaExceededError("document", 1000, 1000, "free")
    )
    client.app.state.ingestion_quota = quota  # type: ignore[attr-defined]

    resp = _upload(client, cid)

    assert resp.status_code == 429, resp.text
    assert embedder.batches == []


def test_upload_with_an_unverifiable_quota_is_refused() -> None:
    embedder = _CountingEmbedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    cid = _create_collection(client)
    quota = MagicMock()
    quota.check_doc_quota = AsyncMock(side_effect=RuntimeError("db down"))
    client.app.state.ingestion_quota = quota  # type: ignore[attr-defined]

    resp = _upload(client, cid)

    assert resp.status_code == 503, resp.text
    assert embedder.batches == []


def test_upload_within_quota_and_budget_is_indexed_and_charged() -> None:
    embedder = _CountingEmbedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    cid = _create_collection(client)
    controller = CostController(per_goal_usd=10.0, per_tenant_daily_usd=10.0)
    client.app.state.cost_controller = controller  # type: ignore[attr-defined]
    quota = MagicMock()
    quota.check_doc_quota = AsyncMock(return_value=None)
    client.app.state.ingestion_quota = quota  # type: ignore[attr-defined]

    resp = _upload(client, cid)

    assert resp.status_code == 201, resp.text
    assert resp.json()["chunks_created"] >= 1
    quota.check_doc_quota.assert_awaited_once_with(_CTX.tenant_id)
    assert controller.daily_total(tenant_ctx=_CTX) > 0
