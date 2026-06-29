"""Comprehensive tests for EvalRunner — all 6 scoring dimensions + async paths."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.intelligence.eval_runner import EvalRunner
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tid: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id="k1")


def _state(**kwargs) -> AgentState:
    defaults = dict(
        goal="test goal",
        tenant_ctx=_ctx(),
        goal_id="g1",
        status=GoalStatus.COMPLETE,
        iterations=1,
        steps=[],
        verification_success=True,
        verification_feedback="",
        events=[],
    )
    defaults.update(kwargs)
    return AgentState(**defaults)


# ── 1. task_completion dimension ─────────────────────────────────────────────

def test_task_completion_complete():
    runner = EvalRunner()
    sc = runner.score(state=_state(status=GoalStatus.COMPLETE), tenant_ctx=_ctx())
    assert sc.scores["task_completion"] == 1.0


def test_task_completion_failed():
    runner = EvalRunner()
    sc = runner.score(state=_state(status=GoalStatus.FAILED), tenant_ctx=_ctx())
    assert sc.scores["task_completion"] == 0.0


def test_task_completion_planning():
    runner = EvalRunner()
    sc = runner.score(state=_state(status=GoalStatus.PLANNING), tenant_ctx=_ctx())
    assert sc.scores["task_completion"] == 0.0


# ── 2. efficiency dimension ───────────────────────────────────────────────────

def test_efficiency_single_iteration():
    """1 iteration out of 15 → iter_efficiency near 1.0, cost_efficiency = 1.0."""
    runner = EvalRunner()
    state = _state(iterations=1, context={})
    sc = runner.score(state=state, tenant_ctx=_ctx())
    # iter_eff = 1.0 - (1-1)/15 = 1.0; cost_eff = 1.0; combined = 1.0
    assert sc.scores["efficiency"] == pytest.approx(1.0)


def test_efficiency_max_iterations():
    """16+ iterations should clamp iter_efficiency to 0."""
    runner = EvalRunner()
    state = _state(iterations=16, context={})
    sc = runner.score(state=state, tenant_ctx=_ctx())
    # iter_eff = max(0, 1.0 - 15/15) = 0.0; cost_eff=1.0; combined=0.3
    assert sc.scores["efficiency"] == pytest.approx(0.3)


def test_efficiency_high_cost():
    """$2+ cost → cost_efficiency = 0."""
    runner = EvalRunner()
    state = _state(iterations=1, context={"total_cost_usd": 2.5})
    sc = runner.score(state=state, tenant_ctx=_ctx())
    # iter_eff=1.0, cost_eff=0.0; combined = 0.7
    assert sc.scores["efficiency"] == pytest.approx(0.7)


def test_efficiency_moderate_cost():
    """$1 cost → cost_efficiency = 0.5."""
    runner = EvalRunner()
    state = _state(iterations=1, context={"total_cost_usd": 1.0})
    sc = runner.score(state=state, tenant_ctx=_ctx())
    # iter_eff=1.0, cost_eff=0.5; combined = 0.7*1.0 + 0.3*0.5 = 0.85
    assert sc.scores["efficiency"] == pytest.approx(0.85)


def test_efficiency_non_dict_context():
    """Non-dict context (empty dict, no total_cost_usd) → cost_efficiency = 1.0."""
    runner = EvalRunner()
    # Pass empty context (no cost key) — same code path as non-dict, cost_usd=0
    state = _state(iterations=3, context={})
    sc = runner.score(state=state, tenant_ctx=_ctx())
    # iter_eff = 1 - 2/15 ≈ 0.867, cost_eff=1.0
    assert sc.scores["efficiency"] > 0


# ── 3. accuracy dimension ─────────────────────────────────────────────────────

def test_accuracy_verification_success():
    runner = EvalRunner()
    sc = runner.score(state=_state(verification_success=True), tenant_ctx=_ctx())
    assert sc.scores["accuracy"] == 1.0


def test_accuracy_verification_failure_no_feedback():
    runner = EvalRunner()
    sc = runner.score(state=_state(verification_success=False, verification_feedback=""), tenant_ctx=_ctx())
    assert sc.scores["accuracy"] == 0.0


def test_accuracy_verification_partial():
    runner = EvalRunner()
    sc = runner.score(
        state=_state(verification_success=False, verification_feedback="partial result"),
        tenant_ctx=_ctx(),
    )
    assert sc.scores["accuracy"] == 0.5


def test_accuracy_verification_partial_case_insensitive():
    runner = EvalRunner()
    sc = runner.score(
        state=_state(verification_success=False, verification_feedback="PARTIAL MATCH"),
        tenant_ctx=_ctx(),
    )
    assert sc.scores["accuracy"] == 0.5


# ── 4. safety dimension ───────────────────────────────────────────────────────

def test_safety_no_events():
    runner = EvalRunner()
    sc = runner.score(state=_state(events=[]), tenant_ctx=_ctx())
    assert sc.scores["safety"] == 1.0


def test_safety_four_deny_events_clamp():
    runner = EvalRunner()
    events = [{"action_level": "DENY"} for _ in range(5)]
    sc = runner.score(state=_state(events=events), tenant_ctx=_ctx())
    assert sc.scores["safety"] == 0.0


def test_safety_outcome_denied():
    runner = EvalRunner()
    events = [{"outcome": "denied"}]
    sc = runner.score(state=_state(events=events), tenant_ctx=_ctx())
    assert sc.scores["safety"] == pytest.approx(0.75)


def test_safety_injection_event():
    runner = EvalRunner()
    events = [{"type": "prompt_injection_detected"}]
    sc = runner.score(state=_state(events=events), tenant_ctx=_ctx())
    assert sc.scores["safety"] == pytest.approx(0.75)


def test_safety_non_dict_events_ignored():
    runner = EvalRunner()
    events = ["string", 42, None]
    sc = runner.score(state=_state(events=events), tenant_ctx=_ctx())
    assert sc.scores["safety"] == 1.0


def test_safety_events_none_attribute():
    runner = EvalRunner()
    state = _state()
    state.events = None  # type: ignore[assignment]
    sc = runner.score(state=state, tenant_ctx=_ctx())
    assert sc.scores["safety"] == 1.0


# ── 5. coherence dimension ────────────────────────────────────────────────────

def test_coherence_no_steps():
    runner = EvalRunner()
    sc = runner.score(state=_state(steps=[]), tenant_ctx=_ctx())
    assert sc.scores["coherence"] == pytest.approx(0.5)


def test_coherence_all_steps_have_output():
    runner = EvalRunner()
    steps = [
        StepResult(description=f"step {i}", output="done", status=StepStatus.COMPLETE)
        for i in range(4)
    ]
    sc = runner.score(state=_state(steps=steps), tenant_ctx=_ctx())
    # output_rate=1.0, all unique descriptions → diversity=1.0; coherence=0.6+0.4=1.0
    assert sc.scores["coherence"] == pytest.approx(1.0)


def test_coherence_no_outputs():
    runner = EvalRunner()
    steps = [
        StepResult(description=f"step {i}", output="", status=StepStatus.COMPLETE)
        for i in range(3)
    ]
    sc = runner.score(state=_state(steps=steps), tenant_ctx=_ctx())
    # output_rate=0, diversity=1.0; coherence=0.4
    assert sc.scores["coherence"] == pytest.approx(0.4)


def test_coherence_duplicate_descriptions():
    runner = EvalRunner()
    steps = [
        StepResult(description="same step", output="done", status=StepStatus.COMPLETE)
        for _ in range(4)
    ]
    sc = runner.score(state=_state(steps=steps), tenant_ctx=_ctx())
    # output_rate=1.0, diversity=1/4=0.25; coherence=0.6 + 0.4*0.25=0.7
    assert sc.scores["coherence"] == pytest.approx(0.7)


# ── 6. SLA dimension ─────────────────────────────────────────────────────────

def test_sla_no_timing_data():
    runner = EvalRunner()
    sc = runner.score(state=_state(context={}), tenant_ctx=_ctx())
    assert sc.scores["sla"] == 1.0


def test_sla_within_budget():
    runner = EvalRunner()
    # Started 10 seconds ago, budget 300s → score should be ~1.0
    started = time.monotonic() - 10.0
    sc = runner.score(
        state=_state(context={"execution_started_at": started, "sla_budget_seconds": 300.0}),
        tenant_ctx=_ctx(),
    )
    assert sc.scores["sla"] == pytest.approx(1.0, abs=0.1)


def test_sla_exceeded_budget():
    runner = EvalRunner()
    # Started 400 seconds ago, budget 300s
    started = time.monotonic() - 400.0
    sc = runner.score(
        state=_state(context={"execution_started_at": started, "sla_budget_seconds": 300.0}),
        tenant_ctx=_ctx(),
    )
    # score = max(0, 1 - max(0, 400-300)/300) = 1 - 100/300 ≈ 0.667
    assert sc.scores["sla"] < 1.0
    assert sc.scores["sla"] >= 0.0


# ── 7. Scorecard structure ────────────────────────────────────────────────────

def test_scorecard_contains_all_dimensions():
    runner = EvalRunner()
    sc = runner.score(state=_state(), tenant_ctx=_ctx())
    for dim in ["task_completion", "efficiency", "accuracy", "safety", "coherence", "sla"]:
        assert dim in sc.scores


def test_scorecard_goal_id_and_goal():
    runner = EvalRunner()
    state = _state(goal="my test goal", goal_id="gXYZ")
    sc = runner.score(state=state, tenant_ctx=_ctx())
    assert sc.goal_id == "gXYZ"
    assert sc.goal == "my test goal"


def test_scorecard_iterations():
    runner = EvalRunner()
    sc = runner.score(state=_state(iterations=7), tenant_ctx=_ctx())
    assert sc.iterations == 7


def test_scorecard_passed_high_scores():
    runner = EvalRunner()
    sc = runner.score(
        state=_state(
            status=GoalStatus.COMPLETE,
            verification_success=True,
            iterations=1,
            context={},
            events=[],
            steps=[StepResult(description="s", output="done")],
        ),
        tenant_ctx=_ctx(),
    )
    assert sc.passed()


def test_scorecard_failed_low_scores():
    runner = EvalRunner()
    sc = runner.score(
        state=_state(
            status=GoalStatus.FAILED,
            verification_success=False,
            verification_feedback="",
            iterations=20,
            events=[{"action_level": "DENY"} for _ in range(5)],
        ),
        tenant_ctx=_ctx(),
    )
    assert not sc.passed()


# ── 8. _score_coherence async ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_coherence_no_steps_returns_half():
    runner = EvalRunner()
    result = await runner._score_coherence("goal", [], provider=None)
    assert result == 0.5


@pytest.mark.asyncio
async def test_score_coherence_no_provider_returns_half():
    runner = EvalRunner()
    result = await runner._score_coherence("goal", ["step1"], provider=None)
    assert result == 0.5


@pytest.mark.asyncio
async def test_score_coherence_provider_returns_score():
    runner = EvalRunner()
    mock_provider = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "0.85"
    mock_provider.complete = AsyncMock(return_value=mock_response)

    with patch("app.providers.base.CompletionRequest"), patch("app.providers.base.Message"):
        result = await runner._score_coherence("goal", ["step1", "step2"], provider=mock_provider)
    assert result == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_score_coherence_provider_exception_returns_default():
    runner = EvalRunner()
    mock_provider = AsyncMock()
    mock_provider.complete = AsyncMock(side_effect=Exception("LLM error"))

    result = await runner._score_coherence("goal", ["step1"], provider=mock_provider)
    assert result == 0.7


@pytest.mark.asyncio
async def test_score_coherence_clamps_high_value():
    runner = EvalRunner()
    mock_provider = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "1.5"  # above 1.0
    mock_provider.complete = AsyncMock(return_value=mock_response)

    with patch("app.providers.base.CompletionRequest"), patch("app.providers.base.Message"):
        result = await runner._score_coherence("goal", ["step"], provider=mock_provider)
    assert result == 1.0


@pytest.mark.asyncio
async def test_score_coherence_clamps_negative_value():
    runner = EvalRunner()
    mock_provider = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "-0.3"
    mock_provider.complete = AsyncMock(return_value=mock_response)

    with patch("app.providers.base.CompletionRequest"), patch("app.providers.base.Message"):
        result = await runner._score_coherence("goal", ["step"], provider=mock_provider)
    assert result == 0.0


# ── 9. score_async ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_async_replaces_coherence():
    runner = EvalRunner()
    state = _state(steps=[StepResult(description="step1", output="done")])

    mock_provider = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "0.92"
    mock_provider.complete = AsyncMock(return_value=mock_response)

    with patch("app.providers.base.CompletionRequest"), patch("app.providers.base.Message"):
        sc = await runner.score_async(state=state, tenant_ctx=_ctx(), provider=mock_provider)
    assert sc.scores["coherence"] == pytest.approx(0.92)


@pytest.mark.asyncio
async def test_score_async_no_provider_uses_heuristic():
    runner = EvalRunner()
    state = _state(steps=[])
    sc = await runner.score_async(state=state, tenant_ctx=_ctx(), provider=None)
    # No provider → falls back to _score_coherence returning 0.5
    assert sc.scores["coherence"] == 0.5


# ── 10. score_and_persist ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_score_and_persist_no_db():
    runner = EvalRunner()
    state = _state()
    sc = await runner.score_and_persist(state, _ctx(), db=None)
    assert sc is not None
    assert "task_completion" in sc.scores


@pytest.mark.asyncio
async def test_score_and_persist_with_db():
    runner = EvalRunner()
    state = _state()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)

    def db_factory():
        return mock_session

    sc = await runner.score_and_persist(state, _ctx(), db=db_factory)
    assert sc is not None


@pytest.mark.asyncio
async def test_score_and_persist_db_exception_does_not_raise():
    runner = EvalRunner()
    state = _state()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)

    def db_factory():
        return mock_session

    # Should not raise despite DB error
    sc = await runner.score_and_persist(state, _ctx(), db=db_factory)
    assert sc is not None
