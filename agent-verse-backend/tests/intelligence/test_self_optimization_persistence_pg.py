"""MEM-26 (integration): v1 self-optimizer suggestions persist under FORCE RLS and
are shared across instances (replicas / restarts).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/intelligence/test_self_optimization_persistence_pg.py -q -m integration
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator

import pytest
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


async def test_suggestions_survive_a_fresh_instance_and_stay_tenant_scoped(
    admin_url: str,
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from app.intelligence.self_optimization import SelfOptimizer
    from app.intelligence.eval import EvalScorecard
    from app.tenancy.context import PlanTier, TenantContext

    ctx_a = TenantContext(tenant_id="so-a", plan=PlanTier.ENTERPRISE, api_key_id="k")
    ctx_b = TenantContext(tenant_id="so-b", plan=PlanTier.ENTERPRISE, api_key_id="k")
    engine = await app_role_engine(admin_url, ["self_optimization_suggestions"])
    factory = sessionmaker_for(engine)

    writer = SelfOptimizer()
    writer._db = factory
    card = EvalScorecard(goal_id="g1", scores={"task_completion": 0.2, "efficiency": 0.1})
    made = await writer.analyze_and_persist(
        goal="g", scorecard=card, error_log="tool not found", tenant_ctx=ctx_a, goal_id="g1"
    )
    assert made

    # Another replica / a restart: a brand-new instance reads them from Postgres.
    reader = SelfOptimizer()
    reader._db = factory
    listed = await reader.alist_suggestions(tenant_ctx=ctx_a)
    assert {s.suggestion_id for s in listed} == {s.suggestion_id for s in made}
    assert await reader.alist_suggestions(tenant_ctx=ctx_b) == []

    # Reject persists and is visible to yet another instance.
    assert await reader.areject_suggestion(
        suggestion_id=made[0].suggestion_id, tenant_ctx=ctx_a
    )
    third = SelfOptimizer()
    third._db = factory
    rejected = {s.suggestion_id for s in await third.alist_suggestions(tenant_ctx=ctx_a)
                if s.rejected}
    assert rejected == {made[0].suggestion_id}
    # Another tenant cannot reject it.
    assert not await third.areject_suggestion(
        suggestion_id=made[1].suggestion_id if len(made) > 1 else made[0].suggestion_id,
        tenant_ctx=ctx_b,
    )
    await engine.dispose()
