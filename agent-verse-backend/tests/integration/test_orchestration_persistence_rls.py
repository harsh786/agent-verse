"""OrchestrationPersistence writes to RLS-protected tables must carry tenant context.

``eval_scorecards`` and ``regression_cases`` both have FORCE ROW LEVEL SECURITY
(migration 0099_reasoning_evaluation_evidence.py) requiring ``app.tenant_id`` to be
set via ``SET LOCAL`` before an INSERT is allowed. ``OrchestrationPersistence
.persist_scorecard`` / ``.persist_regression_case`` opened a plain session and
inserted without ever setting that GUC, so every insert violated the RLS policy —
and the failure was swallowed by the method's own ``except Exception: log.warning``,
so eval scorecards and regression cases were silently never persisted in
production despite the code "succeeding" from the caller's point of view.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.rls import sqlalchemy_rls_context, system_session
from app.evals.runtime_scorecard import ScorecardResult
from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    SecurityConfig,
)
from app.services.orchestration_persistence import OrchestrationPersistence

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse",
)


@pytest.mark.integration
async def test_persist_scorecard_actually_lands_a_row_under_rls() -> None:
    """A real persist_scorecard call must produce a readable eval_scorecards row.

    Before the fix this insert silently violated the FORCE RLS policy on
    eval_scorecards (no app.tenant_id set), the exception was swallowed, and
    this assertion failed because the row was never written.
    """
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = f"orch-{uuid.uuid4().hex[:11]}"
    goal_id = uuid.uuid4().hex
    try:
        async with factory() as session, session.begin(), system_session(session):
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, 'Orch Persist Test', :email, 'free', true)"
                ),
                {"id": tenant_id, "email": f"{tenant_id}@example.test"},
            )

        profile = GoalRuntimeProfile(
            goal_id=goal_id,
            tenant_id=tenant_id,
            properties=GoalProperties(raw_goal="test"),
            agent_patterns=AgentPatternConfig(),
            rag_strategy=RAGStrategyConfig(),
            model_plan=ModelPlanConfig(),
            security=SecurityConfig(),
            memory_cache=MemoryCacheConfig(),
            eval_config=EvalConfig(),
        )
        scorecard = ScorecardResult(
            goal_id=goal_id,
            scores={
                "goal_success": 1.0,
                "rag_quality": 0.8,
                "safety": 1.0,
                "latency": 0.9,
                "cost_efficiency": 0.9,
                "grounding": 0.95,
                "citation_quality": 0.8,
                "retrieval_confidence": 0.7,
                "tool_success_rate": 1.0,
            },
            overall_score=0.91,
        )

        persistence = OrchestrationPersistence(db=factory)
        await persistence.persist_scorecard(scorecard, profile=profile, db=factory)

        async with factory() as session, session.begin(), system_session(session):
            row = (
                await session.execute(
                    text(
                        "SELECT tenant_id, overall_score FROM eval_scorecards "
                        "WHERE goal_id = :goal"
                    ),
                    {"goal": goal_id},
                )
            ).first()
            assert row is not None, (
                "eval_scorecards row was never written — persist_scorecard's insert "
                "is silently failing the FORCE RLS policy (no app.tenant_id set)"
            )
            assert row.tenant_id == tenant_id
            assert row.overall_score == pytest.approx(0.91)
    finally:
        async with factory() as session, session.begin(), system_session(session):
            await session.execute(
                text("DELETE FROM eval_scorecards WHERE goal_id=:goal"), {"goal": goal_id}
            )
            await session.execute(text("DELETE FROM tenants WHERE id=:id"), {"id": tenant_id})
        await engine.dispose()


@pytest.mark.integration
async def test_persist_regression_case_actually_lands_a_row_under_rls() -> None:
    """A real persist_regression_case call must produce a readable regression_cases row.

    Before the fix this insert silently violated the FORCE RLS policy on
    regression_cases (no app.tenant_id set); the row was never written and the
    exception was swallowed by the method's own warning-log except block.
    """
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = f"orch-reg-{uuid.uuid4().hex[:8]}"
    goal_id = uuid.uuid4().hex
    try:
        async with factory() as session, session.begin(), system_session(session):
            # The connecting role (agentverse) is a local-dev superuser with
            # BYPASSRLS, so FORCE ROW LEVEL SECURITY never actually applies to
            # it — a genuine isolation check needs a role without that
            # privilege, same as tests/integration/test_eval_strategy_provenance.py.
            await session.execute(
                text(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles "
                    "WHERE rolname='agentverse_rls_test') THEN "
                    "CREATE ROLE agentverse_rls_test NOLOGIN; "
                    "END IF; END $$"
                )
            )
            await session.execute(text("GRANT USAGE ON SCHEMA public TO agentverse_rls_test"))
            await session.execute(
                text("GRANT SELECT ON regression_cases TO agentverse_rls_test")
            )
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, 'Orch Regression Test', :email, 'free', true)"
                ),
                {"id": tenant_id, "email": f"{tenant_id}@example.test"},
            )

        regression_candidate = {
            "goal_id": goal_id,
            "tenant_id": tenant_id,
            "strategy_id": "react",
            "strategy_version": "1.0.0",
            "profile_version": 1,
            "evaluator_version": "runtime-scorecard-v2",
            "overall_score": 0.2,
        }

        persistence = OrchestrationPersistence(db=factory)
        await persistence.persist_regression_case(regression_candidate, db=factory)

        async with factory() as session, session.begin(), system_session(session):
            row = (
                await session.execute(
                    text(
                        "SELECT tenant_id, strategy_id FROM regression_cases "
                        "WHERE goal_id = :goal"
                    ),
                    {"goal": goal_id},
                )
            ).first()
            assert row is not None, (
                "regression_cases row was never written — persist_regression_case's "
                "insert is silently failing the FORCE RLS policy (no app.tenant_id set)"
            )
            assert row.tenant_id == tenant_id
            assert row.strategy_id == "react"

        # And confirm the RLS policy actually isolates it by tenant, same as the
        # rest of this codebase's tenant-scoped tables — a different tenant's
        # context must see zero rows for this goal.
        async with factory() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE agentverse_rls_test"))
            async with sqlalchemy_rls_context(session, "some-other-tenant"):
                count = await session.scalar(
                    text("SELECT count(*) FROM regression_cases WHERE goal_id=:goal"),
                    {"goal": goal_id},
                )
                assert count == 0
    finally:
        async with factory() as session, session.begin(), system_session(session):
            await session.execute(
                text("DELETE FROM regression_cases WHERE goal_id=:goal"), {"goal": goal_id}
            )
            await session.execute(text("DELETE FROM tenants WHERE id=:id"), {"id": tenant_id})
        await engine.dispose()
