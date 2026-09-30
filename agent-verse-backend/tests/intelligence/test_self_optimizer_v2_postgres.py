"""SelfOptimizerV2 winners reach every replica; unscored goals are recorded (MEM-27).

Applying a winner UPDATEs the ``agents`` row, but the API goal path read the
agent's config (system_prompt ...) from the replica-local AgentStore cache, and
``sync_from_db`` skips keys it already holds — so a replica kept the old prompt
until restart. Goals without an eval score were not recorded at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


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


async def test_winner_reaches_another_replica_and_unscored_goals_are_recorded() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from testcontainers.postgres import PostgresContainer

    from app.api.agents import AgentStore
    from app.intelligence.self_optimizer_v2 import SelfOptimizerV2
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin_url = pg.get_connection_url()
        env = {**os.environ, "DATABASE_URL": admin_url, "ENVIRONMENT": "development"}
        r = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_BACKEND_ROOT, env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"alembic failed:\n{r.stderr[-1500:]}"

        engine = create_async_engine(admin_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        tenant_id = f"t-{uuid.uuid4().hex[:10]}"
        ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")
        async with factory() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, 'Opt', :email, 'professional', true)"
                ),
                {"id": tenant_id, "email": f"{tenant_id}@example.test"},
            )

        # Replica A creates the agent; replica B caches it.
        store_a = AgentStore(factory)
        agent_id = await store_a.create(
            {"name": "writer", "system_prompt": "old prompt"}, tenant_ctx=ctx
        )
        store_b = AgentStore(factory)
        assert (await store_b.get_async(agent_id, tenant_ctx=ctx) or {})["system_prompt"] == (
            "old prompt"
        )
        replica_b = GoalService()
        replica_b._agent_store = store_b

        exp_id = uuid.uuid4().hex
        async with factory() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO improvement_experiments (id, tenant_id, agent_id, name, "
                    "control_config, candidate_config, min_samples_per_arm) VALUES "
                    "(:id, :t, :a, 'prompt test', CAST(:c AS jsonb), CAST(:k AS jsonb), 1)"
                ),
                {
                    "id": exp_id, "t": tenant_id, "a": agent_id,
                    "c": '{"system_prompt": "old prompt"}',
                    "k": '{"system_prompt": "new prompt"}',
                },
            )

        redis = _Redis()
        optimizer = SelfOptimizerV2(redis=redis, db_factory=factory, llm_provider_factory=None)

        # An unscored goal of the running experiment is recorded, marked unscored
        # (eval_score NULL) ...
        await redis.set(
            f"{optimizer._state.PREFIX}{tenant_id}:{agent_id}",
            json.dumps({"goals_completed": 0, "current_experiment_id": exp_id}),
        )
        await optimizer.on_goal_completed(
            tenant_id=tenant_id, agent_id=agent_id, goal_id="",
            eval_score=None, cost_usd=0.0, latency_ms=0,
        )
        async with factory() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT arm, eval_score FROM improvement_results "
                        "WHERE experiment_id = :e"
                    ),
                    {"e": exp_id},
                )
            ).fetchall()
        assert len(rows) == 1 and rows[0][1] is None
        unscored_arm = rows[0][0]
        # ... and is not a sample: with one scored goal on the OTHER arm the
        # experiment (min 1 sample per arm) must not conclude on the unscored one.
        other = "candidate" if unscored_arm == "control" else "control"
        async with factory() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO improvement_results (experiment_id, tenant_id, arm, "
                    "metric_value, metric_name, eval_score) VALUES "
                    "(:e, :t, :arm, 0.9, 'eval_score', 0.9)"
                ),
                {"e": exp_id, "t": tenant_id, "arm": other},
            )
        await optimizer._maybe_conclude_experiment(tenant_id, exp_id)
        async with factory() as s:
            status = (
                await s.execute(
                    text("SELECT status FROM improvement_experiments WHERE id = :e"),
                    {"e": exp_id},
                )
            ).scalar_one()
        assert status == "running"

        # Applying the winner updates the agents row ...
        assert await optimizer.apply_suggestion(
            tenant_id, agent_id, exp_id, {"system_prompt": "new prompt"}
        )
        async with factory() as s:
            applied = (
                await s.execute(
                    text("SELECT status, winner FROM improvement_experiments WHERE id = :e"),
                    {"e": exp_id},
                )
            ).one()
        assert tuple(applied) == ("completed", "candidate")

        # ... and the next goal submitted to replica B uses the new prompt.
        await replica_b.submit_goal(
            goal="write the weekly note", priority="normal", dry_run=True,
            tenant_ctx=ctx, agent_id=agent_id,
        )
        assert (store_b.get(agent_id, tenant_ctx=ctx) or {})["system_prompt"] == "new prompt"
        await engine.dispose()
