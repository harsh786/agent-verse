"""EvalRunner.persist_scorecard on the real schema (integration, MEM-20).

A completion-time score is idempotent (a retry is a no-op); an explicit
re-score replaces the row so every replica's GET /eval reads the new scores.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_rescore_replaces_and_completion_score_is_idempotent() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from testcontainers.postgres import PostgresContainer

    from app.intelligence.eval import EvalScorecard
    from app.intelligence.eval_runner import EvalRunner
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
        goal_id = uuid.uuid4().hex
        async with factory() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, 'Eval', :email, 'free', true)"
                ),
                {"id": tenant_id, "email": f"{tenant_id}@example.test"},
            )
            await s.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                    "autonomy_mode, workflow_mode, execution_context, dry_run, iterations) "
                    "VALUES (:id, :t, 'evaluate', 'complete', 'normal', "
                    "'bounded-autonomous', 'single_agent', '{}', false, 1)"
                ),
                {"id": goal_id, "t": tenant_id},
            )

        ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k")
        runner = EvalRunner()

        def card(score: float) -> EvalScorecard:
            return EvalScorecard(goal_id=goal_id, scores={"accuracy": score, "safety": score})

        assert await runner.persist_scorecard(card(0.2), goal_id=goal_id, tenant_ctx=ctx, db=factory)
        # A retried completion-time score does not overwrite.
        assert await runner.persist_scorecard(card(0.5), goal_id=goal_id, tenant_ctx=ctx, db=factory)
        reader = GoalService()
        reader._db = factory
        got = await reader._persisted_eval(goal_id, ctx)
        assert got is not None and got[0].scores["accuracy"] == pytest.approx(0.2)

        # An explicit re-score replaces it.
        assert await runner.persist_scorecard(
            card(0.9), goal_id=goal_id, tenant_ctx=ctx, db=factory, replace=True, strict=True
        )
        got = await reader._persisted_eval(goal_id, ctx)
        assert got is not None
        assert got[0].scores["accuracy"] == pytest.approx(0.9)
        assert got[1] == pytest.approx(0.9) and got[2] is True
        async with factory() as s:
            count = (
                await s.execute(
                    text("SELECT count(*) FROM evaluations WHERE goal_id = :g"), {"g": goal_id}
                )
            ).scalar_one()
        assert count == 1
        await engine.dispose()
