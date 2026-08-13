from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.state import AgentState, GoalStatus
from app.db.rls import sqlalchemy_rls_context, system_session
from app.intelligence.eval_runner import EvalRunner
from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    SecurityConfig,
    StrategySelection,
)
from app.tenancy.context import PlanTier, TenantContext

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse",
)


@pytest.mark.integration
async def test_eval_provenance_is_idempotent_and_tenant_isolated() -> None:
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = f"eval-{uuid.uuid4().hex[:11]}"
    other_tenant_id = f"eval-{uuid.uuid4().hex[:11]}"
    goal_id = uuid.uuid4().hex
    try:
        async with factory() as session, session.begin():
            async with system_session(session):
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
                    text("GRANT SELECT ON evaluations TO agentverse_rls_test")
                )
                for current in (tenant_id, other_tenant_id):
                    await session.execute(
                        text(
                            "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                            "VALUES (:id, 'Eval Test', :email, 'free', true)"
                        ),
                        {"id": current, "email": f"{current}@example.test"},
                    )
                await session.execute(
                    text(
                        "INSERT INTO goals "
                        "(id, tenant_id, goal_text, status, priority, autonomy_mode, "
                        "workflow_mode, execution_context, dry_run, iterations) "
                        "VALUES (:id, :tenant, 'evaluate', 'completed', 'normal', "
                        "'bounded-autonomous', 'single_agent', '{}', false, 1)"
                    ),
                    {"id": goal_id, "tenant": tenant_id},
                )

        tenant = TenantContext(
            tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="eval-key"
        )
        profile = GoalRuntimeProfile(
            goal_id=goal_id,
            tenant_id=tenant_id,
            properties=GoalProperties(raw_goal="evaluate"),
            agent_patterns=AgentPatternConfig(),
            rag_strategy=RAGStrategyConfig(),
            model_plan=ModelPlanConfig(),
            security=SecurityConfig(),
            memory_cache=MemoryCacheConfig(),
            eval_config=EvalConfig(),
            primary_strategy=StrategySelection("react", "1.0.0"),
        )
        state = AgentState(goal_id=goal_id, goal="evaluate", tenant_ctx=tenant)
        state.status = GoalStatus.COMPLETE
        state.iterations = 1
        state.verification_success = True
        state.context.update(
            {"_runtime_profile": profile, "strategy_execution_id": "execution-1"}
        )
        runner = EvalRunner()
        await runner.score_and_persist(state, tenant, db=factory)
        await runner.score_and_persist(state, tenant, db=factory)

        async with factory() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE agentverse_rls_test"))
            async with sqlalchemy_rls_context(session, tenant_id):
                rows = (
                    await session.execute(
                        text(
                            "SELECT primary_strategy_id, primary_strategy_version, "
                            "profile_version, evaluator_version "
                            "FROM evaluations WHERE goal_id=:goal"
                        ),
                        {"goal": goal_id},
                    )
                ).all()
                assert rows == [("react", "1.0.0", 2, EvalRunner.EVALUATOR_VERSION)]

        async with factory() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE agentverse_rls_test"))
            async with sqlalchemy_rls_context(session, other_tenant_id):
                count = await session.scalar(
                    text("SELECT count(*) FROM evaluations WHERE goal_id=:goal"),
                    {"goal": goal_id},
                )
                assert count == 0

        wrong_tenant = TenantContext(
            tenant_id=other_tenant_id, plan=PlanTier.FREE, api_key_id="other-key"
        )
        with pytest.raises(PermissionError, match="tenant"):
            await runner.score_and_persist(state, wrong_tenant, db=factory)
    finally:
        async with factory() as session, session.begin():
            async with system_session(session):
                await session.execute(
                    text("DELETE FROM evaluations WHERE goal_id=:goal"), {"goal": goal_id}
                )
                await session.execute(
                    text("DELETE FROM goals WHERE id=:goal"), {"goal": goal_id}
                )
                await session.execute(
                    text("DELETE FROM tenants WHERE id IN (:one, :two)"),
                    {"one": tenant_id, "two": other_tenant_id},
                )
        await engine.dispose()
