"""Tests for OrgIntelligence — app/org/intelligence.py"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_intelligence_returns_work_items():
    from app.org.intelligence import WorkValueEngine
    engine = WorkValueEngine()
    assert engine is not None


@pytest.mark.asyncio
async def test_intelligence_work_value_engine_scores():
    from app.org.intelligence import WorkItem, WorkValueEngine
    engine = WorkValueEngine()
    item = WorkItem(
        id="w1",
        title="Fix critical customer issue",
        org_id="org1",
        source="kpi_deviation",
        urgency=0.9,
        strategic_fit=0.8,
    )
    score = engine.score(item)
    assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_intelligence_high_urgency_scores_high():
    from app.org.intelligence import WorkItem, WorkValueEngine
    engine = WorkValueEngine()
    critical = WorkItem(
        id="w2", title="Critical", org_id="org1", source="blocker",
        urgency=1.0, strategic_fit=1.0,
    )
    low_prio = WorkItem(
        id="w3", title="Low", org_id="org1", source="blocker",
        urgency=0.1, strategic_fit=0.1,
    )
    assert engine.score(critical) > engine.score(low_prio)


@pytest.mark.asyncio
async def test_intelligence_prioritize_returns_sorted():
    from app.org.intelligence import WorkItem, WorkValueEngine
    engine = WorkValueEngine()
    items = [
        WorkItem(
            id="w1", title="Low", org_id="org1", source="overdue",
            urgency=0.1, strategic_fit=0.2,
        ),
        WorkItem(
            id="w2", title="Critical", org_id="org1", source="overdue",
            urgency=0.9, strategic_fit=0.9,
        ),
        WorkItem(
            id="w3", title="Medium", org_id="org1", source="overdue",
            urgency=0.5, strategic_fit=0.5,
        ),
    ]
    ranked = engine.rank(items)
    assert ranked[0].id == "w2"   # critical first
