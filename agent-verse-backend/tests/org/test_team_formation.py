"""Tests for TeamFormationEngine — app/org/team_formation.py"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.org.team_formation import RoleMapper, TeamFormationEngine, TeamManifest

# ── Happy path ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_team_formation_simple_goal():
    """Single-domain goal forms a small team."""
    engine = TeamFormationEngine()
    manifest = await engine.form_team_for_goal("Research our top 3 competitors", org_id="org1", tenant_id="t1")
    assert isinstance(manifest, TeamManifest)
    assert manifest.org_id == "org1"
    assert len(manifest.roles) >= 1


@pytest.mark.asyncio
async def test_team_formation_multi_domain_goal():
    """Multi-domain goal includes multiple departments."""
    engine = TeamFormationEngine()
    manifest = await engine.form_team_for_goal(
        "Launch our product in Germany — market research, legal compliance, localization",
        org_id="org1",
        tenant_id="t1",
    )
    dept_names = [r.department for r in manifest.roles]
    assert any("legal" in d.lower() or "strategy" in d.lower() for d in dept_names)
    assert manifest.estimated_agents >= 2


@pytest.mark.asyncio
async def test_team_formation_returns_cost_estimate():
    """TeamManifest includes cost and duration estimate."""
    engine = TeamFormationEngine()
    manifest = await engine.form_team_for_goal("Analyze Q3 sales data", org_id="org1", tenant_id="t1")
    assert manifest.estimated_cost_usd >= 0.0
    assert manifest.estimated_duration_hours > 0.0


@pytest.mark.asyncio
async def test_team_formation_risk_assessment():
    """High-risk goals are flagged."""
    engine = TeamFormationEngine()
    manifest = await engine.form_team_for_goal(
        "Deploy new code to production and delete old database", org_id="org1", tenant_id="t1"
    )
    assert manifest.risk_level in ("medium", "high", "critical")


# ── Role mapper ────────────────────────────────────────────────────────────────

def test_role_mapper_web_search():
    mapper = RoleMapper()
    roles = mapper.map_capabilities(["web_search"])
    assert any("research" in r.name.lower() or "analyst" in r.name.lower() for r in roles)


def test_role_mapper_code_generation():
    mapper = RoleMapper()
    roles = mapper.map_capabilities(["code_generation"])
    assert any("engineer" in r.name.lower() or "developer" in r.name.lower() for r in roles)


def test_role_mapper_legal_review():
    mapper = RoleMapper()
    roles = mapper.map_capabilities(["legal_review"])
    assert any("legal" in r.department.lower() or "compliance" in r.department.lower() for r in roles)


# ── Edge cases ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_team_formation_empty_goal_returns_default():
    """Empty goal should not raise — return minimal default team."""
    engine = TeamFormationEngine()
    manifest = await engine.form_team_for_goal("", org_id="org1", tenant_id="t1")
    assert isinstance(manifest, TeamManifest)
    assert manifest.org_id == "org1"


@pytest.mark.asyncio
async def test_team_formation_budget_constraint():
    """Budget constraint is reflected in team composition."""
    engine = TeamFormationEngine()
    manifest = await engine.form_team_for_goal(
        "Research competitors",
        org_id="org1",
        tenant_id="t1",
        budget_usd=5.0,
    )
    assert manifest.estimated_cost_usd <= 10.0  # soft constraint
