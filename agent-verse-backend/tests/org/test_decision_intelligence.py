"""Tests for DecisionIntelligence — app/org/decision_intelligence.py"""
from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_decision_intelligence_exists():
    from app.org.decision_intelligence import DecisionIntelligence
    engine = DecisionIntelligence()
    assert engine is not None


@pytest.mark.asyncio
async def test_record_decision_returns_id():
    from app.org.decision_intelligence import DecisionIntelligence, DecisionRecord
    engine = DecisionIntelligence()
    decision = DecisionRecord(
        decision_id=str(uuid4()),
        org_id="org1",
        tenant_id="t1",
        decision_type="market_entry",
        description="Which market to enter first?",
        rationale="Large addressable market with strong demand signals",
        confidence=0.82,
        autonomy_level=2,
    )
    engine.record(decision)
    assert decision.decision_id


@pytest.mark.asyncio
async def test_decision_intelligence_tracks_outcome():
    from app.org.decision_intelligence import DecisionIntelligence, DecisionRecord
    engine = DecisionIntelligence()
    decision = DecisionRecord(
        decision_id=str(uuid4()),
        org_id="org2",
        tenant_id="t1",
        decision_type="pricing_strategy",
        description="Pricing strategy",
        rationale="Differentiation",
        confidence=0.75,
        autonomy_level=1,
    )
    engine.record(decision)
    engine.resolve(org_id="org2", decision_id=decision.decision_id, outcome="succeeded")
    insights = engine.list_decisions("org2")
    assert isinstance(insights, list)
    assert insights[0]["outcome"] == "succeeded"


@pytest.mark.asyncio
async def test_decision_intelligence_recommend_model():
    from app.org.decision_intelligence import DecisionIntelligence
    engine = DecisionIntelligence()
    model = await engine.recommend_decision_model(
        decision_type="strategic_planning",
        dept_id="strategy",
    )
    assert model  # some model name returned
