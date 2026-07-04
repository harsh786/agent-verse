"""Test that eval scores actually persist to DB using the real schema."""
import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from app.intelligence.eval_runner import EvalRunner
from app.intelligence.eval import EvalScorecard
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.tenancy.context import TenantContext, PlanTier


def _make_tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-eval-persist-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


def _make_state() -> AgentState:
    tenant_ctx = _make_tenant()
    state = AgentState(goal="test goal", tenant_ctx=tenant_ctx)
    state.goal_id = "goal-eval-001"
    state.status = GoalStatus.COMPLETE
    state.iterations = 3
    state.context = {"total_cost_usd": 0.05}
    state.steps = []
    state.verification_success = True
    return state


@pytest.mark.asyncio
async def test_score_and_persist_writes_correct_schema():
    """score_and_persist must write to scores (JSON) and average_score columns."""
    runner = EvalRunner()
    tenant = _make_tenant()
    state = _make_state()

    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO evaluations" in sql:
            captured["params"] = params
            captured["sql"] = sql
        return MagicMock(fetchone=lambda: None, fetchall=lambda: [])

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=fake_execute)
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.begin = MagicMock(return_value=fake_session)

    def fake_db():
        return fake_session

    scorecard = await runner.score_and_persist(state, tenant, db=fake_db)

    assert "scores" in captured.get("sql", ""), (
        "INSERT must use 'scores' JSON column, not individual score columns. "
        f"Got SQL: {captured.get('sql', 'NONE')}"
    )
    assert "average_score" in captured.get("sql", ""), (
        "INSERT must include 'average_score' column"
    )
    params = captured.get("params", {})
    scores_val = params.get("scores")
    assert scores_val is not None, "scores param must not be None"
    parsed = json.loads(scores_val)
    assert "task_completion" in parsed, "scores JSON must contain task_completion"
    assert "efficiency" in parsed, "scores JSON must contain efficiency"
    avg = params.get("avg")
    assert isinstance(avg, float), f"average_score must be float, got {type(avg)}"
    assert 0.0 <= avg <= 1.0, f"average_score must be in [0,1], got {avg}"


@pytest.mark.asyncio
async def test_score_and_persist_returns_scorecard_even_if_db_fails():
    """score_and_persist must return a scorecard even when DB write fails."""
    runner = EvalRunner()
    tenant = _make_tenant()
    state = _make_state()

    def broken_db():
        raise RuntimeError("DB connection refused")

    scorecard = await runner.score_and_persist(state, tenant, db=broken_db)
    assert scorecard is not None
    assert 0.0 <= scorecard.average_score() <= 1.0
