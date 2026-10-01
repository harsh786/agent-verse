"""KB-49: a whole-collection re-embed is charged to the tenant and metered.

Real Postgres + Redis (testcontainers, through ``test_backends``): the worker
task reserves each batch against the registered cost controller. A refusal
mid-run fails the job and leaves the collection on its OLD dimension/table.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/scaling/test_reembed_metering_integration.py -m integration
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.embedding import usage
from app.governance.cost import BudgetConfig, RedisCostController
from app.providers.base import EmbedResponse
from app.providers.embedder_factory import EmbedderResolution
from app.providers.guarded_completion import set_platform_cost_services

pytestmark = pytest.mark.integration


async def _seed(pg_url: str, n_chunks: int) -> tuple[str, str]:
    tid, cid = f"t-{uuid.uuid4().hex[:10]}", uuid.uuid4().hex
    engine = create_async_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :e, 'enterprise', true)"
                ),
                {"id": tid, "e": f"{tid}@example.test"},
            )
            await conn.execute(
                text(
                    "INSERT INTO knowledge_collections (id, tenant_id, name, embedding_dim) "
                    "VALUES (:id, :tid, :id, 768)"
                ),
                {"id": cid, "tid": tid},
            )
            for i in range(n_chunks):
                await conn.execute(
                    text(
                        "INSERT INTO knowledge_chunks_768 (id, tenant_id, collection_id, "
                        "document_id, chunk_index, content, content_hash, metadata, embedding) "
                        "VALUES (:id, :tid, :cid, 'doc', :i, :c, :h, '{}'::jsonb, "
                        "CAST(:v AS vector))"
                    ),
                    {
                        "id": f"ch-{i:04d}-{cid[:6]}",
                        "tid": tid,
                        "cid": cid,
                        "i": i,
                        "c": f"paragraph number {i}",
                        "h": uuid.uuid4().hex,
                        "v": "[" + ",".join(["0.01"] * 768) + "]",
                    },
                )
    finally:
        await engine.dispose()
    return tid, cid


async def _state(pg_url: str, cid: str) -> tuple[int, int, int]:
    engine = create_async_engine(pg_url)
    try:
        async with engine.connect() as conn:
            dim = (
                await conn.execute(
                    text("SELECT embedding_dim FROM knowledge_collections WHERE id = :c"),
                    {"c": cid},
                )
            ).scalar_one()
            old = (
                await conn.execute(
                    text("SELECT count(*) FROM knowledge_chunks_768 WHERE collection_id = :c"),
                    {"c": cid},
                )
            ).scalar_one()
            new = (
                await conn.execute(
                    text("SELECT count(*) FROM knowledge_chunks_1024 WHERE collection_id = :c"),
                    {"c": cid},
                )
            ).scalar_one()
            return int(dim), int(old), int(new)
    finally:
        await engine.dispose()


def _embedder() -> Any:
    embedder = MagicMock()

    async def _embed(request: Any) -> EmbedResponse:
        return EmbedResponse(embeddings=[[0.2] * 1024 for _ in request.texts])

    embedder.embed = AsyncMock(side_effect=_embed)
    embedder.aclose = AsyncMock()
    return embedder


async def _run(test_backends: tuple[str, str], *, daily_usd: float, n_chunks: int) -> Any:
    pg_url, redis_url = test_backends
    tid, cid = await _seed(pg_url, n_chunks)
    client: Any = aioredis.from_url(redis_url)
    controller = RedisCostController(
        client, per_tenant_config={tid: BudgetConfig(per_tenant_daily_usd=daily_usd)}
    )
    set_platform_cost_services(lambda: (controller, None))
    usage.configure_usage_redis(client)
    embedder = _embedder()
    resolution = EmbedderResolution(embedder=embedder, provider="dedicated", model="m-1024")
    from app.scaling.tasks import re_embed_collection_async

    try:
        with patch("app.providers.embedder_factory.resolve_embedder", return_value=resolution):
            result = await re_embed_collection_async(tid, cid)
        spent = float(await client.get(controller._daily_key(tid)) or 0)
        tokens = await client.hgetall(f"emb:usage:{tid}")
    finally:
        usage.configure_usage_redis(None)
        set_platform_cost_services(None)
        await client.aclose()
    return result, spent, tokens, embedder, await _state(pg_url, cid)


async def test_reembed_reserves_every_batch_and_records_usage(
    test_backends: tuple[str, str],
) -> None:
    result, spent, tokens, embedder, state = await _run(test_backends, daily_usd=10.0, n_chunks=120)
    assert "error" not in result, result
    assert result["re_embedded"] == 120
    batches = embedder.embed.await_count  # 50-chunk re-embed batches
    assert batches == 3
    assert spent == pytest.approx(batches * 0.0001)
    assert int(tokens[b"dedicated/m-1024"]) == 120 * 3  # "paragraph number N"
    assert state == (1024, 0, 120)


async def test_budget_refusal_mid_run_fails_and_keeps_the_old_dimension(
    test_backends: tuple[str, str],
) -> None:
    # Room for exactly one 0.0001 reservation: the second batch is refused.
    result, spent, _tokens, embedder, state = await _run(
        test_backends, daily_usd=0.00015, n_chunks=120
    )
    assert "error" in result
    assert embedder.embed.await_count == 1
    assert spent == pytest.approx(0.0001)
    dim, old_rows, _new_rows = state
    assert (dim, old_rows) == (768, 120)  # the collection never flipped
