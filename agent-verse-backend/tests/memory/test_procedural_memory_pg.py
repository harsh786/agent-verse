"""MEM-11 (integration): procedural memory keeps real stats and recalls relevantly.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_procedural_memory_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT = "proc-pg-t1"


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url, "a91c3e5f7b20")
        yield url


def _state(goal: str, tools: list[str]):  # type: ignore[no-untyped-def]
    from app.agent.state import AgentState, StepResult, StepStatus
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="k")
    st = AgentState(goal=goal, tenant_ctx=ctx)
    st.steps.append(
        StepResult(
            description="s",
            status=StepStatus.COMPLETE,
            tool_calls=[{"tool_name": t, "success": True} for t in tools],
        )
    )
    return st, ctx


async def _seed_tenant(admin_url: str) -> None:
    admin = create_async_engine(admin_url)
    async with admin.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email) VALUES (:t, 'proc', :e) "
                "ON CONFLICT DO NOTHING"
            ),
            {"t": TENANT, "e": f"{TENANT}@example.test"},
        )
    await admin.dispose()


async def test_migration_merges_duplicate_pattern_rows(admin_url: str) -> None:
    await _seed_tenant(admin_url)
    admin = create_async_engine(admin_url)
    async with admin.begin() as conn:
        await conn.execute(text("ALTER TABLE procedural_memories NO FORCE ROW LEVEL SECURITY"))
        await conn.execute(
            text(
                "INSERT INTO procedural_memories (id, tenant_id, goal_pattern, domain, "
                "tool_sequence, use_count, success_rate) VALUES "
                "('d1', :t, 'dup pattern', 'general', '[\"a\"]', 1, 1.0), "
                "('d2', :t, 'dup pattern', 'general', '[\"a\"]', 3, 0.0)"
            ),
            {"t": TENANT},
        )
        await conn.execute(text("ALTER TABLE procedural_memories FORCE ROW LEVEL SECURITY"))
    await admin.dispose()

    alembic_upgrade(admin_url, "head")

    admin = create_async_engine(admin_url)
    async with admin.begin() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT})
        rows = (
            await conn.execute(
                text(
                    "SELECT use_count, success_rate FROM procedural_memories "
                    "WHERE goal_pattern = 'dup pattern'"
                )
            )
        ).fetchall()
    await admin.dispose()
    assert len(rows) == 1
    assert rows[0][0] == 4 and rows[0][1] == pytest.approx(0.25)


async def test_learn_upserts_real_stats_and_recall_ranks_by_pattern(admin_url: str) -> None:
    alembic_upgrade(admin_url, "head")
    await _seed_tenant(admin_url)
    from app.memory.procedural import ProceduralMemoryStore

    engine = await app_role_engine(admin_url, ["procedural_memories"])
    factory = sessionmaker_for(engine)
    goal = "Triage the failing nightly payments reconciliation job"
    # Two successes and one failure, each from a fresh store (cache miss path).
    for success in (True, True, False):
        st, ctx = _state(goal, ["jira_search", "slack_post"])
        await ProceduralMemoryStore(db_factory=factory).learn(
            state=st, tenant_ctx=ctx, success=success
        )

    async with factory() as s, s.begin():
        await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT})
        rows = (
            await s.execute(
                text(
                    "SELECT use_count, success_rate FROM procedural_memories "
                    "WHERE tenant_id = :t AND goal_pattern LIKE 'Triage%'"
                ),
                {"t": TENANT},
            )
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 3
    assert rows[0][1] == pytest.approx(2 / 3, abs=0.01)

    # 50 unrelated high-rate skills must not crowd out the matching one.
    for i in range(50):
        st, ctx = _state(f"Generate quarterly marketing deck variant {chr(65 + i % 26)}{i}",
                         ["docs_create"])
        await ProceduralMemoryStore(db_factory=factory).learn(state=st, tenant_ctx=ctx)

    recalled = await ProceduralMemoryStore(db_factory=factory).recall(
        goal="triage failing payments reconciliation job", tenant_id=TENANT,
        min_success_rate=0.5, limit=2,
    )
    assert recalled and recalled[0].goal_pattern.startswith("Triage")
    assert recalled[0].use_count == 3
    await engine.dispose()
