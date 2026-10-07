"""``goal_cost_breakdowns.fallback_from`` on a real, migrated Postgres.

The per-role record names the model that SERVED a call; the models it failed
over from accumulate (distinct) in ``fallback_from`` (migration e1f3a5c7b9d2).
This runs the real UPSERT / SELECT of ``app.observability.cost_breakdown``
against ``alembic upgrade head``.

Run with::

    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/integration/test_cost_breakdown_fallback_postgres.py -q --no-cov
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.observability import cost_breakdown as cbd

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def bound_db(pg_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(pg_url)
    cbd.configure_db(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield
    finally:
        cbd.reset_db()
        await engine.dispose()


async def test_fallback_from_accumulates_distinct_models_and_reads_back(bound_db: None) -> None:
    tenant = str(uuid.uuid4())
    goal_id = f"g-{uuid.uuid4().hex}"

    await cbd.arecord_role_cost(goal_id, "executor", "qwen", 10, 2, 0.0, tenant_id=tenant,
                                fallback_from=["dead-a"])
    await cbd.arecord_role_cost(goal_id, "executor", "qwen", 10, 2, 0.0, tenant_id=tenant,
                                fallback_from=["dead-a", "dead-b"])
    await cbd.arecord_role_cost(goal_id, "executor", "qwen", 10, 2, 0.0, tenant_id=tenant)
    await cbd.arecord_role_cost(goal_id, "planner", "qwen", 5, 1, 0.0, tenant_id=tenant)

    bd = await cbd.aget_breakdown(goal_id, tenant_id=tenant)
    rows = {(e.role, e.model): e for e in bd.entries}

    assert set(rows) == {("executor", "qwen"), ("planner", "qwen")}
    assert rows[("executor", "qwen")].calls == 3
    assert sorted(rows[("executor", "qwen")].fallback_from) == ["dead-a", "dead-b"]
    assert rows[("planner", "qwen")].fallback_from == []
