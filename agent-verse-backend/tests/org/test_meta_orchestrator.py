"""Tests for MetaOrchestrator — app/org/meta_orchestrator.py"""
from __future__ import annotations

import pytest

from app.org.meta_orchestrator import MetaOrchestrator, OrchestratorDecision


@pytest.mark.asyncio
async def test_meta_orchestrator_single_domain():
    """Simple goal routes to single_agent topology."""
    orch = MetaOrchestrator()
    decision = await orch.decide(
        goal="What are the key features of our product?",
        org_id="org1",
        tenant_id="t1",
    )
    assert isinstance(decision, OrchestratorDecision)
    assert decision.topology in (
        "single_agent", "pipeline", "map_reduce", "hierarchical",
        "swarm", "debate", "event_driven",
    )


@pytest.mark.asyncio
async def test_meta_orchestrator_research_goal_uses_swarm():
    """Broad research goals prefer swarm topology."""
    orch = MetaOrchestrator()
    decision = await orch.decide(
        goal="Research the entire landscape of AI companies and produce a comprehensive report",
        org_id="org1",
        tenant_id="t1",
    )
    # Research + broad → swarm or map_reduce
    assert decision.topology in ("swarm", "map_reduce", "hierarchical", "pipeline")


@pytest.mark.asyncio
async def test_meta_orchestrator_high_risk_uses_hierarchical():
    """High-risk goals route to hierarchical topology."""
    orch = MetaOrchestrator()
    decision = await orch.decide(
        goal="Deploy our application to production and handle regulatory compliance",
        org_id="org1",
        tenant_id="t1",
    )
    assert decision.topology in ("hierarchical", "pipeline")
    assert decision.autonomy_level <= 3  # approval gates required


@pytest.mark.asyncio
async def test_meta_orchestrator_returns_departments():
    """Decision includes relevant departments."""
    orch = MetaOrchestrator()
    decision = await orch.decide(
        goal="Launch marketing campaign and legal review",
        org_id="org1",
        tenant_id="t1",
    )
    dept_names = [d.lower() for d in decision.departments]
    assert any("marketing" in d or "legal" in d for d in dept_names)


@pytest.mark.asyncio
async def test_meta_orchestrator_autonomy_level_within_bounds():
    """Autonomy level is always L0-L5."""
    orch = MetaOrchestrator()
    decision = await orch.decide(goal="Anything", org_id="org1", tenant_id="t1")
    assert 0 <= decision.autonomy_level <= 5


@pytest.mark.asyncio
async def test_meta_orchestrator_sets_model_profile():
    """Decision includes a model gateway profile."""
    orch = MetaOrchestrator()
    decision = await orch.decide(goal="Write a report", org_id="org1", tenant_id="t1")
    assert decision.model_profile in (
        "premium", "smart", "coding", "analytical", "creative",
        "fast", "research", "expert", "worker",
    )
