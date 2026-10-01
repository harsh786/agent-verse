"""KB-40/48/49: embedding batches are reserved against the shared Redis budget.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/embedding/test_embedding_metering_integration.py -m integration
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import redis.asyncio as aioredis

from app.embedding import usage
from app.embedding.metering import EmbeddingBudgetExceededError, embed_metered
from app.governance.cost import BudgetConfig, RedisCostController
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


async def test_redis_cost_controller_is_charged_per_batch_and_refuses_past_budget(
    redis_url: str,
) -> None:
    client: Any = aioredis.from_url(redis_url)
    usage.configure_usage_redis(client)
    try:
        tid = f"t-{uuid.uuid4().hex[:8]}"
        ctx = TenantContext(tid, PlanTier.PROFESSIONAL, "k")
        controller = RedisCostController(
            client, per_tenant_config={tid: BudgetConfig(per_tenant_daily_usd=0.00035)}
        )
        sent: list[int] = []

        async def _batch(batch: list[str]) -> list[list[float]]:
            sent.append(len(batch))
            return [[0.1] * 4 for _ in batch]

        await embed_metered(
            [f"w {i}" for i in range(192)],
            _batch,
            tenant_ctx=ctx,
            model="m",
            controller=controller,
        )
        assert sent == [64, 64, 64]
        spent = float(await client.get(controller._daily_key(tid)))
        assert spent == pytest.approx(3 * 0.0001)
        assert int((await client.hgetall(f"emb:usage:{tid}"))[b"m"]) == 384

        # The 4th reservation would pass 0.00035: refused before it is sent.
        with pytest.raises(EmbeddingBudgetExceededError):
            await embed_metered(
                ["a", "b"], _batch, tenant_ctx=ctx, model="m", controller=controller
            )
        assert sent == [64, 64, 64]
        assert float(await client.get(controller._daily_key(tid))) == pytest.approx(spent)
    finally:
        usage.configure_usage_redis(None)
        await client.aclose()
