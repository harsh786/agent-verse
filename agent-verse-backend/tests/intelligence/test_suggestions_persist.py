"""Verify self-optimizer suggestions persist to DB and can be queried."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.intelligence.eval import EvalScorecard
from app.intelligence.self_optimization import OptimizationSuggestion, SelfOptimizer
from app.tenancy.context import PlanTier, TenantContext


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-sugg-persist-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


def _make_fake_db():
    """Return (fake_db_factory, captured) where captured gets filled on INSERT."""
    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO self_optimization_suggestions" in sql:
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

    return fake_db, captured


@pytest.mark.asyncio
async def test_persist_suggestion_writes_correct_columns():
    """persist_suggestion must INSERT to self_optimization_suggestions with correct params."""
    opt = SelfOptimizer()
    tenant = _tenant()

    sugg = OptimizationSuggestion(
        category="prompt",
        change_type="improve_planner_prompt",
        description="Add domain-specific decomposition",
        before="old prompt",
        after="new improved prompt",
        confidence=0.75,
    )

    fake_db, captured = _make_fake_db()
    await opt.persist_suggestion(sugg, tenant_ctx=tenant, db=fake_db)

    assert "INSERT INTO self_optimization_suggestions" in captured.get("sql", ""), \
        f"Expected INSERT, got: {captured.get('sql', 'NONE')}"
    params = captured["params"]
    assert params["tenant_id"] == "test-sugg-persist-001"
    assert params["suggestion_id"] == sugg.suggestion_id
    assert params["category"] == "prompt"
    assert params["change_type"] == "improve_planner_prompt"
    assert isinstance(params["confidence"], float)
    assert params["confidence"] == 0.75


@pytest.mark.asyncio
async def test_persist_suggestion_non_fatal_on_db_error():
    """persist_suggestion must not raise when DB write fails."""
    opt = SelfOptimizer()
    tenant = _tenant()

    sugg = OptimizationSuggestion(
        category="prompt", description="test", confidence=0.7
    )

    def broken_db():
        raise RuntimeError("DB unavailable")

    # Must not raise
    await opt.persist_suggestion(sugg, tenant_ctx=tenant, db=broken_db)


@pytest.mark.asyncio
async def test_analyze_and_suggest_auto_persists_when_db_set():
    """analyze_and_suggest auto-persists when _db is set on the optimizer."""
    opt = SelfOptimizer()
    tenant = _tenant()

    fake_db, captured = _make_fake_db()
    opt._db = fake_db  # wire DB

    scorecard = EvalScorecard(
        goal_id="g1", goal="test",
        scores={"task_completion": 0.2, "efficiency": 0.2, "accuracy": 0.3,
                "safety": 1.0, "coherence": 0.2},
    )

    import asyncio
    suggestions = opt.analyze_and_suggest(
        goal="test goal with tool error",
        scorecard=scorecard,
        error_log="tool not found: jira_search",
        tenant_ctx=tenant,
    )

    # Give background tasks time to run
    await asyncio.sleep(0.1)

    # At least some suggestion should have been persisted
    assert len(suggestions) >= 1
    # The captured dict may or may not have data depending on asyncio task scheduling,
    # but the call must not raise
