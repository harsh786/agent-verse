"""MEM-01 (integration): tool reliability learns from real calls under FORCE RLS.

* A failing MCP tool call dispatched by the executor increments
  ``failure_count`` in ``tool_reliability_memory`` under a NOBYPASSRLS role.
* The executor reads it back (same store, fresh instance) and deprioritises it.
* The migration turns pre-existing synthetic BLACKLIST failures into a flag.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_tool_reliability_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT = "rel-pg-tenant"
OTHER = "rel-pg-other"


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        # Seed a synthetic BLACKLIST row at the previous head, then migrate.
        alembic_upgrade(url, "d4e9a1c7b3f2")
        yield url


async def test_migration_converts_synthetic_blacklist_rows(admin_url: str) -> None:
    admin = create_async_engine(admin_url)
    async with admin.begin() as conn:
        await conn.execute(text("ALTER TABLE tool_reliability_memory NO FORCE ROW LEVEL SECURITY"))
        await conn.execute(
            text(
                "INSERT INTO tool_reliability_memory (tenant_id, tool_name, success_count, "
                "failure_count, total_latency_ms) VALUES "
                "(:t, 'synthetic_tool', 0, 2, 10000.0), (:t, 'real_tool', 3, 1, 420.0)"
            ),
            {"t": TENANT},
        )
        await conn.execute(text("ALTER TABLE tool_reliability_memory FORCE ROW LEVEL SECURITY"))
    await admin.dispose()

    alembic_upgrade(admin_url, "head")

    from app.memory.tool_reliability import ToolReliabilityStore

    engine = await app_role_engine(admin_url, ["tool_reliability_memory"])
    store = ToolReliabilityStore(db_session_factory=sessionmaker_for(engine))
    synthetic = await store.get_reliability(tenant_id=TENANT, tool_name="synthetic_tool")
    assert synthetic["failure_count"] == 0
    assert synthetic["blacklisted"] is True
    real = await store.get_reliability(tenant_id=TENANT, tool_name="real_tool")
    assert (real["success_count"], real["failure_count"], real["blacklisted"]) == (3, 1, False)
    await engine.dispose()


async def test_failing_mcp_call_increments_failure_count_under_rls(admin_url: str) -> None:
    alembic_upgrade(admin_url, "head")
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState, StepResult, StepStatus
    from app.agent.tool_context import ToolContext, ToolRef
    from app.memory.tool_reliability import ToolReliabilityStore
    from app.providers.fake import FakeProvider
    from app.tenancy.context import PlanTier, TenantContext

    class _Result:
        success = False
        output = None
        error = "HTTP 502"

    class _FailingMCP:
        async def call_tool(self, **_kw: object) -> object:
            return _Result()

    ctx = TenantContext(tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="k1")
    engine = await app_role_engine(admin_url, ["tool_reliability_memory"])
    factory = sessionmaker_for(engine)
    tool_ctx = ToolContext(
        connectors=[],
        tools=[
            ToolRef(server_id=n, server_name="Custom", name=n, description=n, input_schema={})
            for n in ("get_flaky_status", "get_other_status")
        ],
    )

    for _ in range(3):
        graph = AgentGraph(
            planner=FakeProvider(responses=["plan"]),
            executor=FakeProvider(responses=['{"tool": "get_flaky_status", "arguments": {}}', "done"]),
            verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
            mcp_client=_FailingMCP(),
            tool_reliability_store=ToolReliabilityStore(db_session_factory=factory),
        )
        state = AgentState(goal="g", tenant_ctx=ctx)
        state.steps.append(StepResult(description="check", status=StepStatus.RUNNING))
        state.context["tool_context"] = tool_ctx
        await graph._execute_step("check", state, ctx)

    fresh = ToolReliabilityStore(db_session_factory=factory)
    stats = await fresh.get_reliability(tenant_id=TENANT, tool_name="get_flaky_status")
    assert stats["failure_count"] == 3 and stats["success_count"] == 0
    # RLS: another tenant sees nothing.
    assert await fresh.list_tools(tenant_id=OTHER) == []

    # The next step deprioritises the tool learned as unreliable.
    executor = FakeProvider(responses=["done"])
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=executor,
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        tool_reliability_store=fresh,
    )
    state = AgentState(goal="g", tenant_ctx=ctx)
    state.steps.append(StepResult(description="check", status=StepStatus.RUNNING))
    state.context["tool_context"] = tool_ctx
    await graph._execute_step("check", state, ctx)
    assert [t.name for t in executor.call_history[0].tools] == ["get_other_status", "get_flaky_status"]
    assert state.context["_unreliable_tools"] == ["get_flaky_status"]
    await engine.dispose()
