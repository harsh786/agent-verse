"""VOYAGER-PERSIST (real Postgres): the voyager skill library is durable and tenant-scoped.

Runs as a least-privilege (NOBYPASSRLS) role so ``voyager_skills``'s FORCE RLS
is really enforced:

* publish -> readable from a brand-new store/engine (another replica, a
  restart); re-publishing the identical skill is idempotent; a different
  contract under the same version is refused; UPDATE is refused by trigger;
* another tenant sees nothing;
* a fake-provider voyager goal through the real StrategyRunner publishes into
  Postgres, and a second executor (another replica) sees the skill.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.memory.procedural_validator import ProcedureContract
from app.memory.voyager_skills_pg import PostgresVoyagerSkillStore

pytestmark = pytest.mark.integration

TENANT_A = uuid.uuid4().hex
TENANT_B = uuid.uuid4().hex
_CTX = {
    "available_tools": {},
    "allowed_capabilities": frozenset({"llm_reasoning"}),
    "ready_connectors": frozenset(),
    "policy_fingerprint": "policy-1",
}


@pytest_asyncio.fixture
async def app_url(pg_url: str) -> AsyncIterator[str]:
    role = f"test_voyager_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(18)
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        quoted = (await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})).scalar()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        db = (await conn.execute(text("SELECT current_database()"))).scalar()
        await conn.execute(text(f'GRANT CONNECT ON DATABASE "{db}" TO {role}'))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE ON voyager_skills TO {role}"))
        await conn.execute(text(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role}"))
    await admin.dispose()
    yield make_url(pg_url).set(username=role, password=password).render_as_string(
        hide_password=False
    )


def _factory(url: str) -> tuple[Any, Any]:
    engine = create_async_engine(url, pool_size=2, max_overflow=0)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _skill(tenant: str, version: str = "v1", tools: tuple[str, ...] = ()) -> ProcedureContract:
    return ProcedureContract(
        procedure_id="voyager-abc",
        tenant_id=tenant,
        skill_version=version,
        tool_sequence=tools,
        required_capabilities=frozenset({"llm_reasoning"}),
        tool_schema_versions={},
        connector_ids=frozenset(),
        policy_fingerprint="policy-1",
    )


@pytest.mark.asyncio
async def test_skills_are_durable_immutable_and_tenant_scoped(app_url: str) -> None:
    engine, factory = _factory(app_url)
    try:
        store = PostgresVoyagerSkillStore(factory)
        published = await store.publish(
            _skill(TENANT_A), provenance={"goal_id": "g1", "steps": ["a", "b"]}, **_CTX
        )
        assert published == _skill(TENANT_A)
        # Idempotent re-publish of the identical skill.
        assert await store.publish(_skill(TENANT_A), **_CTX) == _skill(TENANT_A)
        # Same version, different contract -> refused.
        with pytest.raises(ValueError, match="immutable"):
            await store.publish(_skill(TENANT_A, tools=("other",)), **_CTX)
        # Validation still runs before anything is stored.
        with pytest.raises(PermissionError):
            await store.publish(_skill(TENANT_A, "v2"), **{**_CTX, "policy_fingerprint": "x"})
        # UPDATE is refused by the trigger even for the owning tenant.
        async with factory() as s, s.begin():
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A})
            with pytest.raises(Exception, match="immutable"):
                await s.execute(text("UPDATE voyager_skills SET goal_id = 'x'"))
    finally:
        await engine.dispose()

    # A brand-new engine (another replica / after a restart) reads it back.
    engine2, factory2 = _factory(app_url)
    try:
        replica = PostgresVoyagerSkillStore(factory2)
        row = await replica.get(TENANT_A, "voyager-abc", "v1")
        assert row is not None
        assert row["contract"] == _skill(TENANT_A)
        assert row["steps"] == ["a", "b"] and row["goal_id"] == "g1"
        assert [r["skill_version"] for r in await replica.list(TENANT_A)] == ["v1"]
        # Another tenant sees nothing (RLS + explicit predicate).
        assert await replica.get(TENANT_B, "voyager-abc", "v1") is None
        assert await replica.list(TENANT_B) == []
    finally:
        await engine2.dispose()


@pytest.mark.asyncio
async def test_fake_provider_voyager_goal_persists_its_skill(app_url: str) -> None:
    from app.orchestration.strategy_context_store import (
        StrategyGoalContext,
        StrategyGoalContextStore,
    )
    from app.orchestration.strategy_contracts import (
        ExecutionTerminalState,
        PatternLimits,
        StrategyExecutionRequest,
    )
    from app.orchestration.strategy_executor import (
        DistributedStrategyExecutor,
        default_distributed_admission,
    )
    from app.orchestration.strategy_registry import build_default_registry
    from app.orchestration.strategy_runner import StrategyRunner
    from app.providers.fake import FakeProvider
    from app.tenancy.context import PlanTier, TenantContext

    tenant = uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    engine, factory = _factory(app_url)
    try:
        contexts = StrategyGoalContextStore()
        await contexts.put(
            "ctx-1",
            StrategyGoalContext(
                goal_text="Explain why the sky is blue",
                provider=FakeProvider(
                    responses=[
                        '{"steps": [{"id": "s1", "summary": "collect facts"}]}',
                        "Rayleigh scattering",
                        "Final: Rayleigh scattering makes the sky blue.",
                    ]
                ),
                tenant_ctx=ctx,
            ),
        )
        runner = StrategyRunner(
            build_default_registry(),
            executor=DistributedStrategyExecutor(
                context_store=contexts, skill_store=PostgresVoyagerSkillStore(factory)
            ),
            admission=default_distributed_admission,
        )
        request = StrategyExecutionRequest.model_validate(
            {
                "tenant_id": tenant,
                "goal_id": "goal-pg-1",
                "strategy_id": "voyager",
                "adapter_version": "1.0.0",
                "state_schema_version": 1,
                "agent_id": "agent-1",
                "runtime_profile_ref": "profile-1",
                "context_snapshot_ref": "ctx-1",
                "policy_ref": "policy-pg",
                "budget_ref": "budget-1",
                "cancellation_token": "goal-pg-1",
                "deadline": datetime.now(UTC) + timedelta(minutes=1),
                "idempotency_key": "idem-pg-1",
            }
        )
        limits = PatternLimits.model_validate(
            {
                "calls": 32, "nodes": 32, "edges": 32, "depth": 8, "fan_out": 8,
                "rounds": 16, "tokens": 20_000, "duration_seconds": 30, "cost_usd": 5.0,
            }
        )
        result = await runner.run(request, limits)
        assert result.terminal_state is ExecutionTerminalState.SUCCEEDED, result
        assert "Rayleigh" in (result.answer or "")
    finally:
        await engine.dispose()

    engine2, factory2 = _factory(app_url)
    try:
        rows = await PostgresVoyagerSkillStore(factory2).list(tenant)
        assert len(rows) == 1
        assert rows[0]["goal_id"] == "goal-pg-1"
        assert rows[0]["steps"] == ["collect facts"]
        assert rows[0]["contract"].policy_fingerprint == "policy-pg"
    finally:
        await engine2.dispose()
