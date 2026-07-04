"""Smoke test: all 4 self-improvement DB tables can receive rows via fake DB.

This verifies the write paths work end-to-end using fake DB factories,
so it runs without a real Postgres container.
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from app.tenancy.context import TenantContext, PlanTier


def _tenant(suffix: str = "001") -> TenantContext:
    return TenantContext(
        tenant_id=f"test-e2e-smoke-{suffix}",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


def _make_capturing_db(table_filter: str):
    """Return (db_factory, captured_list) where captured_list grows on INSERT."""
    captured = []

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO" in sql and table_filter in sql:
            captured.append({"sql": sql, "params": params})
        return MagicMock(fetchone=lambda: None, fetchall=lambda: [])

    session = MagicMock()
    session.execute = AsyncMock(side_effect=fake_execute)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=session)

    def db_factory():
        return session

    return db_factory, captured


# ── Layer 1: ExecutionMemory ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execution_memory_writes_to_db():
    """ExecutionMemory.record_async must INSERT into execution_memory table."""
    from app.memory.execution import ExecutionMemory
    mem = ExecutionMemory()
    db, captured = _make_capturing_db("execution_memory")

    await mem.record_async(
        goal="test Jira search",
        plan=["step 1: search", "step 2: filter"],
        success=True,
        tenant_id="test-e2e-smoke-001",
        db=db,
    )

    assert len(captured) == 1, "ExecutionMemory must produce 1 INSERT"
    assert "execution_memory" in captured[0]["sql"]
    params = captured[0]["params"]
    assert params["tid"] == "test-e2e-smoke-001"
    assert "step 1" in params["goal"] or "Jira" in params["goal"]


# ── Layer 2: LongTermMemory ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_long_term_memory_writes_to_db():
    """LongTermMemoryStore.store_async must INSERT into long_term_memory table."""
    from app.memory.long_term import LongTermMemoryStore, LongTermMemory
    store = LongTermMemoryStore()
    tenant = _tenant("002")
    db, captured = _make_capturing_db("long_term_memory")

    memory = LongTermMemory(
        content="Successful goal: search Jira → found 12 tickets",
        source_goal_id="goal-ltm-001",
        memory_type="success_pattern",
        confidence=0.8,
        tags=["jira"],
    )
    await store.store_async(memory=memory, tenant_ctx=tenant, db=db)

    assert len(captured) == 1, "LongTermMemory must produce 1 INSERT"
    params = captured[0]["params"]
    assert params["tid"] == "test-e2e-smoke-002"
    assert "12 tickets" in params["content"]


# ── Layer 3: Evaluations ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_eval_runner_writes_to_evaluations():
    """EvalRunner.score_and_persist must INSERT into evaluations with scores JSON."""
    from app.intelligence.eval_runner import EvalRunner
    from app.agent.state import AgentState, GoalStatus
    runner = EvalRunner()
    tenant = _tenant("003")
    db, captured = _make_capturing_db("evaluations")

    state = AgentState(goal="test goal", tenant_ctx=tenant)
    state.goal_id = "goal-eval-smoke-001"
    state.status = GoalStatus.COMPLETE
    state.iterations = 2
    state.context = {"total_cost_usd": 0.01}
    state.steps = []

    await runner.score_and_persist(state, tenant, db=db)

    assert len(captured) == 1, "EvalRunner must produce 1 INSERT into evaluations"
    params = captured[0]["params"]
    assert params["tid"] == "test-e2e-smoke-003"
    scores = json.loads(params["scores"])
    assert "task_completion" in scores
    assert isinstance(params["avg"], float)
    assert isinstance(params["passed"], bool)


# ── Layer 4: Self-Optimization Suggestions ────────────────────────────────────

@pytest.mark.asyncio
async def test_self_optimizer_writes_to_suggestions_table():
    """SelfOptimizer.persist_suggestion must INSERT into self_optimization_suggestions."""
    from app.intelligence.self_optimization import SelfOptimizer, OptimizationSuggestion
    opt = SelfOptimizer()
    tenant = _tenant("004")
    db, captured = _make_capturing_db("self_optimization_suggestions")

    sugg = OptimizationSuggestion(
        category="prompt",
        change_type="improve_planner_prompt",
        description="Add domain context",
        before="old",
        after="new",
        confidence=0.8,
    )
    await opt.persist_suggestion(sugg, tenant_ctx=tenant, db=db)

    assert len(captured) == 1, "SelfOptimizer must produce 1 INSERT"
    params = captured[0]["params"]
    assert params["tenant_id"] == "test-e2e-smoke-004"
    assert params["category"] == "prompt"
    assert params["confidence"] == 0.8
