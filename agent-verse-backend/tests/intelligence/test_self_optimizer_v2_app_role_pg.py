"""a05-F087-03: SelfOptimizerV2's agent writes run under RLS as the app role.

The existing Postgres test (test_self_optimizer_v2_postgres.py) runs on the
container superuser, which bypasses every RLS policy — so it could not catch an
``agents`` UPDATE (or its history / experiment bookkeeping) that only works
without RLS, nor one that reaches another tenant's row. These run the same
writes as a NOSUPERUSER / NOBYPASSRLS login role, the production app role's
shape, against FORCE ROW LEVEL SECURITY tables.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/intelligence/test_self_optimizer_v2_app_role_pg.py -q -m integration
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT_A, TENANT_B = "f087-tenant-a", "f087-tenant-b"
_TABLES = ["agents", "improvement_experiments", "improvement_results",
           "agent_optimization_history"]


class _Redis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def set(self, key: str, value: Any, *a: Any, **k: Any) -> None:
        self.kv[key] = value

    async def setex(self, key: str, ttl: int, value: Any) -> None:
        self.kv[key] = value

    async def incr(self, key: str) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return int(self.kv[key])

    async def expire(self, *a: Any, **k: Any) -> None:
        return None


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


@pytest.fixture(scope="module")
async def admin(admin_url: str) -> AsyncIterator[async_sessionmaker[Any]]:
    engine = create_async_engine(admin_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s, s.begin():
        for t in (TENANT_A, TENANT_B):
            await s.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :email, 'professional', true)"
                ),
                {"id": t, "email": f"{t}@example.test"},
            )
    yield factory
    await engine.dispose()


@pytest.fixture(scope="module")
async def app_engine(admin_url: str, admin: Any) -> AsyncIterator[AsyncEngine]:
    engine = await app_role_engine(admin_url, _TABLES)
    yield engine
    await engine.dispose()


async def _agent(admin: Any, tenant_id: str, *, autonomy: str = "bounded-autonomous") -> str:
    from app.api.agents import AgentStore
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    agent_id = await AgentStore(admin).create(
        {"name": "writer", "system_prompt": "old prompt"}, tenant_ctx=ctx
    )
    if autonomy != "bounded-autonomous":
        async with admin() as s, s.begin():
            await s.execute(
                text("UPDATE agents SET autonomy_mode = :m WHERE id = :id"),
                {"m": autonomy, "id": agent_id},
            )
    return str(agent_id)


async def _experiment(admin: Any, tenant_id: str, agent_id: str) -> str:
    exp_id = uuid.uuid4().hex
    async with admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO improvement_experiments (id, tenant_id, agent_id, name, "
                "control_config, candidate_config, min_samples_per_arm) VALUES "
                "(:id, :t, :a, 'prompt test', CAST(:c AS jsonb), CAST(:k AS jsonb), 1)"
            ),
            {
                "id": exp_id, "t": tenant_id, "a": agent_id,
                "c": json.dumps({"system_prompt": "old prompt",
                                 "autonomy_mode": "fully-autonomous"}),
                "k": json.dumps({"system_prompt": "new prompt"}),
            },
        )
    return exp_id


async def _prompt(admin: Any, agent_id: str) -> tuple[str, str]:
    async with admin() as s:
        row = (
            await s.execute(
                text("SELECT system_prompt, autonomy_mode FROM agents WHERE id = :id"),
                {"id": agent_id},
            )
        ).one()
    return str(row[0]), str(row[1])


def _optimizer(app_engine: AsyncEngine) -> Any:
    from app.intelligence.self_optimizer_v2 import SelfOptimizerV2

    return SelfOptimizerV2(
        redis=_Redis(), db_factory=sessionmaker_for(app_engine), llm_provider_factory=None
    )


async def test_app_role_is_bound_by_rls(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert tuple(row) == (False, False)


async def test_apply_and_its_bookkeeping_work_under_rls(admin: Any, app_engine: Any) -> None:
    agent_id = await _agent(admin, TENANT_A)
    exp_id = await _experiment(admin, TENANT_A, agent_id)

    assert await _optimizer(app_engine).apply_suggestion(
        TENANT_A, agent_id, exp_id, {"system_prompt": "new prompt"}
    )

    assert await _prompt(admin, agent_id) == ("new prompt", "bounded-autonomous")
    async with admin() as s:
        exp = (
            await s.execute(
                text(
                    "SELECT status, winner, applied_at IS NOT NULL "
                    "FROM improvement_experiments WHERE id = :e"
                ),
                {"e": exp_id},
            )
        ).one()
        history = (
            await s.execute(
                text(
                    "SELECT tenant_id, config_after ->> 'system_prompt' "
                    "FROM agent_optimization_history WHERE experiment_id = :e"
                ),
                {"e": exp_id},
            )
        ).all()
    assert tuple(exp) == ("completed", "candidate", True)
    assert [tuple(h) for h in history] == [(TENANT_A, "new prompt")]


async def test_apply_never_reaches_another_tenants_agent(admin: Any, app_engine: Any) -> None:
    victim = await _agent(admin, TENANT_B)
    exp_id = await _experiment(admin, TENANT_A, victim)

    assert not await _optimizer(app_engine).apply_suggestion(
        TENANT_A, victim, exp_id, {"system_prompt": "hijacked"}
    )
    assert await _prompt(admin, victim) == ("old prompt", "bounded-autonomous")
    async with admin() as s:
        count = (
            await s.execute(
                text("SELECT count(*) FROM agent_optimization_history WHERE agent_id = :a"),
                {"a": victim},
            )
        ).scalar_one()
    assert count == 0


async def test_rollback_restores_the_control_config_under_rls(
    admin: Any, app_engine: Any
) -> None:
    agent_id = await _agent(admin, TENANT_A)
    exp_id = await _experiment(admin, TENANT_A, agent_id)
    optimizer = _optimizer(app_engine)
    assert await optimizer.apply_suggestion(
        TENANT_A, agent_id, exp_id, {"system_prompt": "new prompt"}
    )

    assert await optimizer.rollback(TENANT_A, agent_id, exp_id, "regressed")

    # The control snapshot says fully-autonomous; the agent never was — the
    # rollback restores the prompt and never promotes it (a05-F095-04).
    assert await _prompt(admin, agent_id) == ("old prompt", "bounded-autonomous")
    async with admin() as s:
        status = (
            await s.execute(
                text("SELECT status FROM improvement_experiments WHERE id = :e"), {"e": exp_id}
            )
        ).scalar_one()
    assert status == "rolled_back"


async def test_rollback_never_reaches_another_tenants_agent(
    admin: Any, app_engine: Any
) -> None:
    victim = await _agent(admin, TENANT_B)
    async with admin() as s, s.begin():
        await s.execute(
            text("UPDATE agents SET system_prompt = 'b current' WHERE id = :id"), {"id": victim}
        )
    exp_id = await _experiment(admin, TENANT_A, victim)

    assert not await _optimizer(app_engine).rollback(TENANT_A, victim, exp_id, "x")
    assert await _prompt(admin, victim) == ("b current", "bounded-autonomous")


async def test_a_fully_autonomous_agents_config_is_not_applied_behind_the_gate(
    admin: Any, app_engine: Any
) -> None:
    agent_id = await _agent(admin, TENANT_A, autonomy="fully-autonomous")
    exp_id = await _experiment(admin, TENANT_A, agent_id)

    result = await _optimizer(app_engine).apply_pending(TENANT_A, exp_id)
    assert result["reason"] == "winner_is_unset"  # not concluded yet
    async with admin() as s, s.begin():
        await s.execute(
            text("UPDATE improvement_experiments SET winner = 'candidate' WHERE id = :e"),
            {"e": exp_id},
        )

    result = await _optimizer(app_engine).apply_pending(TENANT_A, exp_id)
    assert result == {
        "applied": False, "agent_id": agent_id, "experiment_id": exp_id,
        "reason": "rollout_gate",
    }
    assert await _prompt(admin, agent_id) == ("old prompt", "fully-autonomous")
