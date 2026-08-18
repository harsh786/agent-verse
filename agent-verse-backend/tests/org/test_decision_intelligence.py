"""Tests for DecisionIntelligenceEngine — app/org/decision_intelligence.py"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_decision_intelligence_exists():
    from app.org.decision_intelligence import DecisionIntelligenceEngine
    engine = DecisionIntelligenceEngine()
    assert engine is not None


@pytest.mark.asyncio
async def test_record_decision_returns_id():
    from app.org.decision_intelligence import DecisionIntelligenceEngine, OrgDecision
    engine = DecisionIntelligenceEngine()
    decision = OrgDecision(
        mission_id="m1",
        agent_id="agent-1",
        dept_id="marketing",
        problem="Which market to enter first?",
        options=[{"label": "Germany", "pros": ["large market"], "cons": ["regulatory complexity"]}],
        chosen_option="Germany",
        rationale="Large addressable market with strong demand signals",
        confidence=0.82,
        risk_level="medium",
    )
    decision_id = await engine.record_decision(decision)
    assert decision_id


@pytest.mark.asyncio
async def test_decision_intelligence_tracks_outcome():
    from app.org.decision_intelligence import DecisionIntelligenceEngine, OrgDecision
    engine = DecisionIntelligenceEngine()
    decision = OrgDecision(
        mission_id="m2",
        agent_id="agent-2",
        dept_id="strategy",
        problem="Pricing strategy",
        options=[],
        chosen_option="Premium",
        rationale="Differentiation",
        confidence=0.75,
        risk_level="low",
    )
    decision_id = await engine.record_decision(decision)
    await engine.measure_outcome(decision_id, actual_outcome="Premium tier adopted with +15% ARR")
    insights = await engine.get_decision_insights("strategy")
    assert isinstance(insights, list)


@pytest.mark.asyncio
async def test_decision_intelligence_recommend_model():
    from app.org.decision_intelligence import DecisionIntelligenceEngine
    engine = DecisionIntelligenceEngine()
    model = await engine.recommend_decision_model(
        decision_type="strategic_planning",
        dept_id="strategy",
    )
    assert model  # some model name returned
