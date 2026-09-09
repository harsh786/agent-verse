"""Tests for OrgSimulationEngine — app/org/loop_detector.py"""
from __future__ import annotations

import pytest

from app.org.loop_detector import OrgSimulationEngine


@pytest.mark.asyncio
async def test_estimate_simple_goal():
    engine = OrgSimulationEngine()
    est = await engine.estimate_mission("Research our competitors")
    assert est.estimated_agents >= 1
    assert est.estimated_duration_hours > 0
    assert est.estimated_cost_usd >= 0
    assert 0.0 <= est.confidence <= 1.0
    assert 0.0 <= est.success_probability <= 1.0


@pytest.mark.asyncio
async def test_estimate_complex_goal_has_more_depts():
    engine = OrgSimulationEngine()
    est_simple  = await engine.estimate_mission("Write a blog post")
    est_complex = await engine.estimate_mission(
        "Launch in Germany: legal, marketing, engineering, finance, HR"
    )
    assert len(est_complex.departments_needed) >= len(est_simple.departments_needed)


@pytest.mark.asyncio
async def test_estimate_legal_goal_includes_blockers():
    engine = OrgSimulationEngine()
    est = await engine.estimate_mission("GDPR compliance review and legal contract analysis")
    assert any("legal" in b.lower() or "review" in b.lower() for b in est.potential_blockers)


@pytest.mark.asyncio
async def test_chaos_test_key_agent_fails():
    engine = OrgSimulationEngine()
    result = await engine.chaos_test({"goal": "Research"}, "key_agent_fails")
    assert result.can_auto_recover is True
    assert result.estimated_recovery_hours > 0


@pytest.mark.asyncio
async def test_chaos_test_unknown_scenario_graceful():
    engine = OrgSimulationEngine()
    result = await engine.chaos_test({"goal": "Research"}, "unknown_scenario_xyz")
    assert result.scenario == "unknown_scenario_xyz"
    assert result.can_auto_recover is False
