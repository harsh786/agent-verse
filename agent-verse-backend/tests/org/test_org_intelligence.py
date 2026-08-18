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
    from app.org.intelligence import WorkValueEngine, WorkItem
    engine = WorkValueEngine()
    item = WorkItem(
        id="w1",
        title="Fix critical customer issue",
        urgency=0.9,
        strategic_alignment=0.8,
        effort_hours=2.0,
    )
    score = engine.score(item)
    assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_intelligence_high_urgency_scores_high():
    from app.org.intelligence import WorkValueEngine, WorkItem
    engine = WorkValueEngine()
    critical = WorkItem(id="w2", title="Critical", urgency=1.0, strategic_alignment=1.0, effort_hours=1.0)
    low_prio  = WorkItem(id="w3", title="Low",      urgency=0.1, strategic_alignment=0.1, effort_hours=8.0)
    assert engine.score(critical) > engine.score(low_prio)


@pytest.mark.asyncio
async def test_intelligence_prioritize_returns_sorted():
    from app.org.intelligence import WorkValueEngine, WorkItem
    engine = WorkValueEngine()
    items = [
        WorkItem(id="w1", title="Low",      urgency=0.1, strategic_alignment=0.2, effort_hours=8.0),
        WorkItem(id="w2", title="Critical", urgency=0.9, strategic_alignment=0.9, effort_hours=1.0),
        WorkItem(id="w3", title="Medium",   urgency=0.5, strategic_alignment=0.5, effort_hours=4.0),
    ]
    ranked = engine.prioritize(items)
    assert ranked[0].id == "w2"   # critical first
