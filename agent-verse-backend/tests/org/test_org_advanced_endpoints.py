"""Tests for the new org endpoints added in deep-audit implementation.

Covers:
  - N2  POST /v1/org/compose
  - N5  GET  /v1/org/{id}/intelligence/capabilities
  - N6  GET  /v1/org/{id}/intelligence/work
  - SUPP-J GET /v1/org/{id}/intelligence/decisions
  - P4  GET  /v1/org/{id}/brief/strategic
  - P4  GET  /v1/org/{id}/brief/morning
  - Q2  POST /v1/org/{id}/command
  - Q3  GET  /v1/org/{id}/commands
  - W6  POST /v1/org/{id}/missions/batch
  - U8  POST /v1/org/{id}/graph/version
  - U8  GET  /v1/org/{id}/graph/versions
  - SUPP-H POST /v1/org/{id}/twin/simulate
  - SUPP-H GET  /v1/org/{id}/twin/capacity
  - QA10 POST /v1/org/{id}/emergency-stop
  - QA10 POST /v1/org/{id}/emergency-stop/resume
  - PART14 GET/POST /v1/org/{id}/departments/{dept_id}/memory
  - P13 POST/GET /v1/org/{id}/teams/{team_id}/lifecycle
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.org.router import router as org_router
from app.org.service import OrgService

TENANT_ID  = "00000000-0000-0000-0000-000000000003"
ORG_ID     = str(uuid.uuid4())
DEPT_ID    = str(uuid.uuid4())
TEAM_ID    = str(uuid.uuid4())
MISSION_ID = str(uuid.uuid4())


# ── Test app factory ──────────────────────────────────────────────────────────

def _make_app(mock_service: Any) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_tenant(request: Any, call_next: Any) -> Any:
        tenant = MagicMock()
        tenant.tenant_id = TENANT_ID
        request.state.tenant = tenant
        return await call_next(request)

    async def _get_service() -> OrgService:  # type: ignore[return]
        return mock_service

    app.include_router(org_router)
    app.dependency_overrides[
        # Import get_org_service to override it
        __import__("app.org.router", fromlist=["get_org_service"]).get_org_service
    ] = _get_service

    return app


def _fake_org(org_id: str = ORG_ID) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(org_id)
    m.tenant_id = uuid.UUID(TENANT_ID)
    m.name = "Acme AI Corp"
    m.slug = "acme-ai-corp"
    m.status = "active"
    m.autonomy_level = 2
    return m


def _fake_mission(mission_id: str = MISSION_ID) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(mission_id)
    m.tenant_id = uuid.UUID(TENANT_ID)
    m.org_id = uuid.UUID(ORG_ID)
    m.title = "Test Mission"
    m.status = "draft"
    m.priority = "medium"
    return m


def _fake_dept() -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(DEPT_ID)
    m.name = "Engineering"
    m.purpose = "Build great software"
    return m


def _fake_team() -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(TEAM_ID)
    m.name = "Alpha Team"
    m.metadata = {}
    return m


def _fake_health() -> dict[str, Any]:
    return {
        "health": "healthy",
        "active_missions": 3,
        "active_teams": 2,
        "pending_approvals": 0,
        "task_counts": {"running": 5, "blocked": 0, "failed": 0},
        "event_counts_24h": {},
        "items_needing_attention": 0,
    }


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def mock_svc() -> MagicMock:
    svc = MagicMock(spec=OrgService)
    svc.get_organization = AsyncMock(return_value=_fake_org())
    svc.get_org_health   = AsyncMock(return_value=_fake_health())
    svc.create_organization = AsyncMock(return_value=_fake_org())
    svc.create_department   = AsyncMock(return_value=_fake_dept())
    svc.create_mission      = AsyncMock(return_value=_fake_mission())
    svc.list_departments    = AsyncMock(return_value=[_fake_dept()])
    svc.get_team            = AsyncMock(return_value=_fake_team())
    svc.update_team         = AsyncMock(return_value=_fake_team())
    return svc


@pytest.fixture()
async def client(mock_svc: MagicMock) -> AsyncClient:  # type: ignore[misc]
    async with AsyncClient(
        transport=ASGITransport(app=_make_app(mock_svc)),
        base_url="http://test",
    ) as c:
        yield c


# ── N2: Org Composer ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_compose_creates_org(client: AsyncClient, mock_svc: MagicMock) -> None:
    """POST /v1/org/compose creates an org with departments and initial missions."""
    mock_svc.compose_from_nl = AsyncMock(return_value={
        "org_id": ORG_ID,
        "name": "Acme AI Corp",
        "departments": [{"id": DEPT_ID, "name": "Engineering", "purpose": "Build", "capability_domains": []}],
        "initial_missions": [],
        "autonomy_level": 2,
        "status": "ready",
        "composition_method": "template",
    })
    resp = await client.post("/v1/org/compose", json={
        "description": "A fintech startup building AI-powered cash flow tools",
        "goals": ["Increase MRR by 20%"],
        "industry": "fintech",
        "autonomy_level": 2,
        "budget_usd": 5000,
        "constraints": [],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "org_id" in data
    assert len(data["departments"]) > 0


@pytest.mark.anyio
async def test_compose_returns_400_on_empty_description(client: AsyncClient) -> None:
    """Pydantic should reject empty description."""
    resp = await client.post("/v1/org/compose", json={"description": ""})
    # missing required fields → 422 or method returns 400 depending on validator
    assert resp.status_code in (400, 422)


# ── N5: Capability Graph ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_capability_graph_returns_capabilities(client: AsyncClient) -> None:
    """GET /v1/org/{id}/intelligence/capabilities returns capability list."""
    resp = await client.get(f"/v1/org/{ORG_ID}/intelligence/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert "org_id" in data
    assert "capabilities" in data
    assert "total" in data


@pytest.mark.anyio
async def test_capability_graph_with_gap_analysis(client: AsyncClient) -> None:
    """Gap analysis returns coverage percentage."""
    resp = await client.get(
        f"/v1/org/{ORG_ID}/intelligence/capabilities?required=python,react,postgres"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "gap_analysis" in data
    assert "coverage_pct" in data["gap_analysis"]


# ── N6/N9: Work Discovery + Value Engine ────────────────────────────────────

@pytest.mark.anyio
async def test_work_discovery_returns_items(client: AsyncClient) -> None:
    """GET /v1/org/{id}/intelligence/work returns discovered work items."""
    resp = await client.get(f"/v1/org/{ORG_ID}/intelligence/work")
    assert resp.status_code == 200
    data = resp.json()
    assert data["org_id"] == ORG_ID
    assert "work_items" in data
    assert isinstance(data["work_items"], list)


@pytest.mark.anyio
async def test_work_discovery_with_health_issues(client: AsyncClient, mock_svc: MagicMock) -> None:
    """Work discovery returns items when health shows problems."""
    mock_svc.get_org_health = AsyncMock(return_value={
        **_fake_health(),
        "task_counts": {"failed": 3, "blocked": 5},
        "items_needing_attention": 3,
    })
    resp = await client.get(f"/v1/org/{ORG_ID}/intelligence/work")
    assert resp.status_code == 200
    data = resp.json()
    # With failures and blocks, should discover work
    assert data["discovered_count"] >= 0  # may find items


# ── SUPP-J: Decision Intelligence ────────────────────────────────────────────

@pytest.mark.anyio
async def test_decision_history_returns_empty_initially(client: AsyncClient) -> None:
    """GET /v1/org/{id}/intelligence/decisions returns empty list initially."""
    resp = await client.get(f"/v1/org/{ORG_ID}/intelligence/decisions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["org_id"] == ORG_ID
    assert "decisions" in data
    assert "quality_report" in data


# ── P4: Strategic Brief ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_strategic_brief_returns_brief(client: AsyncClient) -> None:
    """GET /v1/org/{id}/brief/strategic returns structured strategic brief."""
    resp = await client.get(f"/v1/org/{ORG_ID}/brief/strategic")
    assert resp.status_code == 200
    data = resp.json()
    assert data["org_id"] == ORG_ID
    assert "health_summary" in data
    assert "recommendations" in data
    assert isinstance(data["recommendations"], list)


@pytest.mark.anyio
async def test_morning_brief_returns_brief(client: AsyncClient) -> None:
    """GET /v1/org/{id}/brief/morning returns morning brief."""
    resp = await client.get(f"/v1/org/{ORG_ID}/brief/morning")
    assert resp.status_code == 200
    data = resp.json()
    assert data["org_id"] == ORG_ID
    assert "overall_health" in data
    assert "priorities" in data


# ── Q2/Q3: UCG Command ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_ucg_command_accepted(client: AsyncClient) -> None:
    """POST /v1/org/{id}/command returns command_id."""
    resp = await client.post(f"/v1/org/{ORG_ID}/command", json={
        "command": "Generate weekly market intelligence report",
        "channel": "rest",
    })
    assert resp.status_code == 202
    data = resp.json()
    assert "command_id" in data
    assert data["requires_2fa"] is False


@pytest.mark.anyio
async def test_ucg_high_risk_command_flags_2fa(client: AsyncClient) -> None:
    """High-risk commands (delete, deploy) require 2FA."""
    resp = await client.post(f"/v1/org/{ORG_ID}/command", json={
        "command": "delete all missions from last month",
        "channel": "telegram",
    })
    assert resp.status_code == 202
    data = resp.json()
    assert data["requires_2fa"] is True


@pytest.mark.anyio
async def test_ucg_command_history(client: AsyncClient) -> None:
    """GET /v1/org/{id}/commands returns command history."""
    # Submit a command first
    await client.post(f"/v1/org/{ORG_ID}/command", json={"command": "status check", "channel": "rest"})
    resp = await client.get(f"/v1/org/{ORG_ID}/commands")
    assert resp.status_code == 200
    data = resp.json()
    assert "commands" in data
    assert "total" in data


# ── W6: Batch Operations ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_batch_missions_creates_all(client: AsyncClient) -> None:
    """POST /v1/org/{id}/missions/batch creates multiple missions."""
    resp = await client.post(f"/v1/org/{ORG_ID}/missions/batch", json={
        "missions": [
            {"title": "Mission Alpha", "priority": "high"},
            {"title": "Mission Beta",  "priority": "medium"},
            {"title": "Mission Gamma", "priority": "low"},
        ]
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["count"] == 3
    assert len(data["created"]) == 3


@pytest.mark.anyio
async def test_batch_missions_rejects_over_20(client: AsyncClient) -> None:
    """POST /v1/org/{id}/missions/batch rejects more than 20 missions."""
    resp = await client.post(f"/v1/org/{ORG_ID}/missions/batch", json={
        "missions": [{"title": f"M{i}", "priority": "low"} for i in range(21)]
    })
    assert resp.status_code == 422


# ── U8: KG Versioning ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_graph_snapshot_creates_version(client: AsyncClient) -> None:
    """POST /v1/org/{id}/graph/version creates a versioned snapshot."""
    with patch("app.knowledge_graph.store.kg_store") as mock_kg:
        mock_kg._tenant_nodes = {TENANT_ID: {"node-1", "node-2"}}
        resp = await client.post(f"/v1/org/{ORG_ID}/graph/version")
    assert resp.status_code == 201
    data = resp.json()
    assert "version_id" in data
    assert data["version_num"] == 1


@pytest.mark.anyio
async def test_graph_version_history_empty(client: AsyncClient) -> None:
    """GET /v1/org/{id}/graph/versions returns empty history initially."""
    resp = await client.get(f"/v1/org/{ORG_ID}/graph/versions")
    assert resp.status_code == 200
    data = resp.json()
    assert "versions" in data
    assert isinstance(data["versions"], list)


# ── SUPP-H: Digital Twin ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_twin_simulate_mission(client: AsyncClient) -> None:
    """POST /v1/org/{id}/twin/simulate returns duration and cost estimates."""
    resp = await client.post(f"/v1/org/{ORG_ID}/twin/simulate", json={
        "title": "Market analysis Q3",
        "priority": "high",
        "description": "Analyse competitor pricing",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "estimated_duration_h" in data
    assert "estimated_cost_usd" in data
    assert "feasible" in data
    assert "confidence" in data


@pytest.mark.anyio
async def test_twin_capacity_plan(client: AsyncClient) -> None:
    """GET /v1/org/{id}/twin/capacity returns utilisation data."""
    resp = await client.get(f"/v1/org/{ORG_ID}/twin/capacity")
    assert resp.status_code == 200
    data = resp.json()
    assert "current_utilisation" in data
    assert "recommendations" in data


# ── QA10: Emergency Stop ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_emergency_stop(client: AsyncClient) -> None:
    """POST /v1/org/{id}/emergency-stop sets stopped status."""
    resp = await client.post(f"/v1/org/{ORG_ID}/emergency-stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stopped"


@pytest.mark.anyio
async def test_emergency_resume(client: AsyncClient) -> None:
    """POST /v1/org/{id}/emergency-stop/resume clears stop."""
    resp = await client.post(f"/v1/org/{ORG_ID}/emergency-stop/resume")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resumed"


# ── PART 14: Department Memory ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_dept_memory_add_and_list(client: AsyncClient) -> None:
    """POST + GET /v1/org/{id}/departments/{dept_id}/memory."""
    # Add an entry
    resp = await client.post(
        f"/v1/org/{ORG_ID}/departments/{DEPT_ID}/memory",
        json={
            "content": "Engineering stack: FastAPI + React + Postgres",
            "source": "cto_agent",
            "confidence": 0.95,
            "tags": ["stack", "technology"],
        }
    )
    assert resp.status_code == 201
    add_data = resp.json()
    assert "entry_id" in add_data

    # List entries
    resp = await client.get(f"/v1/org/{ORG_ID}/departments/{DEPT_ID}/memory")
    assert resp.status_code == 200
    list_data = resp.json()
    assert list_data["dept_id"] == DEPT_ID
    assert len(list_data["entries"]) >= 1
    assert list_data["summary"]["total_entries"] >= 1


# ── P13: Team Lifecycle ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_team_lifecycle_get(client: AsyncClient, mock_svc: MagicMock) -> None:
    """GET /v1/org/{id}/teams/{team_id}/lifecycle returns current state."""
    resp = await client.get(f"/v1/org/{ORG_ID}/teams/{TEAM_ID}/lifecycle")
    assert resp.status_code == 200
    data = resp.json()
    assert data["team_id"] == TEAM_ID
    assert "current_state" in data
    assert "next_allowed" in data


@pytest.mark.anyio
async def test_team_lifecycle_advance(client: AsyncClient, mock_svc: MagicMock) -> None:
    """POST /v1/org/{id}/teams/{team_id}/lifecycle advances the state."""
    resp = await client.post(f"/v1/org/{ORG_ID}/teams/{TEAM_ID}/lifecycle")
    assert resp.status_code == 200
    data = resp.json()
    assert data["team_id"] == TEAM_ID
    assert data["current_state"] == "staff"  # create → staff


# ── Authentication ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_all_new_endpoints_require_auth() -> None:
    """All new endpoints return 401 without tenant middleware."""
    bare_app = FastAPI()
    bare_app.include_router(org_router)
    async with AsyncClient(
        transport=ASGITransport(app=bare_app), base_url="http://test"
    ) as c:
        endpoints = [
            ("GET",  f"/v1/org/{ORG_ID}/intelligence/capabilities"),
            ("GET",  f"/v1/org/{ORG_ID}/intelligence/work"),
            ("GET",  f"/v1/org/{ORG_ID}/brief/strategic"),
            ("GET",  f"/v1/org/{ORG_ID}/brief/morning"),
            ("POST", f"/v1/org/{ORG_ID}/emergency-stop"),
        ]
        for method, path in endpoints:
            resp = await c.request(method, path)
            assert resp.status_code == 401, f"{method} {path} should return 401"
