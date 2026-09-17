"""Coverage-boost tests for app/org/router.py.

Targets branches left uncovered by tests/org/test_org_router.py and the other
tests/org/*.py files: not-found / cross-org-scoping paths, RBAC-gated
endpoints, task/team/role/schedule CRUD, approval flows, and a handful of the
streaming/websocket endpoints. Uses the same fast in-memory-mock convention as
tests/org/test_org_router.py (dependency override of ``get_org_service``, a
fake tenant middleware, and MagicMock ORM rows) — no real DB or Redis.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager as contextlib_asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.org.router import router as org_router
from app.org.service import OrgService

TENANT_ID = "00000000-0000-0000-0000-000000000001"
ORG_ID = str(uuid.uuid4())
OTHER_ORG_ID = str(uuid.uuid4())
MISSION_ID = str(uuid.uuid4())
DEPT_ID = str(uuid.uuid4())
TASK_ID = str(uuid.uuid4())
TEAM_ID = str(uuid.uuid4())
NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)


def _fake_org(**kw: Any) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(ORG_ID)
    m.tenant_id = uuid.UUID(TENANT_ID)
    m.name = kw.get("name", "Acme AI Corp")
    m.slug = "acme-ai-corp"
    m.description = ""
    m.industry = "technology"
    m.jurisdiction = "US"
    m.mission = "Build world-class AI"
    m.vision = ""
    m.status = kw.get("status", "active")
    m.autonomy_level = kw.get("autonomy_level", 1)
    m.risk_tolerance = "medium"
    m.monthly_budget_usd = 0.0
    m.settings = kw.get("settings", {})
    m.created_by = None
    m.created_at = NOW
    m.updated_at = NOW
    return m


def _fake_mission(org_id: str = ORG_ID, **kw: Any) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(MISSION_ID)
    m.tenant_id = uuid.UUID(TENANT_ID)
    m.org_id = uuid.UUID(org_id)
    m.dept_id = None
    m.assigned_team_id = None
    m.title = kw.get("title", "Test Mission")
    m.objective = "Achieve the goal"
    m.why = "Because it matters"
    m.expected_outcome = "Success"
    m.priority = "medium"
    m.status = kw.get("status", "proposed")
    m.source = "manual"
    m.autonomy_level = 1
    m.budget_usd = 0.0
    m.deadline = None
    m.started_at = None
    m.completed_at = None
    m.tags = []
    m.outputs = []
    m.evidence = []
    m.created_by = None
    m.extra_data = {}
    m.created_at = NOW
    m.updated_at = NOW
    return m


def _fake_dept(**kw: Any) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(DEPT_ID)
    m.tenant_id = uuid.UUID(TENANT_ID)
    m.org_id = uuid.UUID(ORG_ID)
    m.name = kw.get("name", "Engineering")
    m.purpose = "Build things"
    m.parent_dept_id = None
    m.capability_domains = []
    m.manager_agent_id = None
    m.status = "active"
    m.created_at = NOW
    m.updated_at = NOW
    return m


def _fake_task(org_id: str = ORG_ID, **kw: Any) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(TASK_ID)
    m.tenant_id = uuid.UUID(TENANT_ID)
    m.org_id = uuid.UUID(org_id)
    m.mission_id = uuid.UUID(MISSION_ID)
    m.parent_task_id = None
    m.assigned_team_id = None
    m.owner_agent_id = None
    m.title = kw.get("title", "Test Task")
    m.objective = "Do the thing"
    m.status = kw.get("status", "queued")
    m.priority = "medium"
    m.depth = 0
    m.risk_level = kw.get("risk_level", "low")
    m.assigned_agent_ids = []
    m.dependencies = []
    m.outputs = kw.get("outputs", [])
    m.evidence = []
    m.budget_usd = None
    m.actual_cost_usd = kw.get("actual_cost_usd")
    m.started_at = None
    m.completed_at = None
    m.extra_data = kw.get("extra_data", {})
    m.cost_estimate_usd = kw.get("cost_estimate_usd")
    m.expires_at = None
    m.created_at = NOW
    m.updated_at = NOW
    return m


def _fake_team(**kw: Any) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(TEAM_ID)
    m.tenant_id = uuid.UUID(TENANT_ID)
    m.org_id = uuid.UUID(ORG_ID)
    m.dept_id = None
    m.name = kw.get("name", "Squad One")
    m.purpose = "Ship stuff"
    m.team_type = "persistent"
    m.manager_agent_id = None
    m.member_agent_ids = kw.get("member_agent_ids", [])
    m.capability_ids = []
    m.status = "active"
    m.extra_data = kw.get("extra_data", {})
    m.created_at = NOW
    m.updated_at = NOW
    return m


@pytest.fixture
def test_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def fake_tenant(request, call_next):
        from app.tenancy.context import PlanTier, TenantContext

        request.state.tenant = TenantContext(
            tenant_id=TENANT_ID,
            plan=PlanTier.PROFESSIONAL,
            api_key_id="test-key",
            roles=("admin",),
        )
        return await call_next(request)

    app.include_router(org_router)
    return app


@pytest.fixture
def mock_service() -> MagicMock:
    svc = MagicMock(spec=OrgService)
    svc._tenant_id = TENANT_ID
    svc._session = MagicMock()
    svc.get_organization = AsyncMock(return_value=_fake_org())
    svc.update_organization = AsyncMock(return_value=_fake_org())
    svc.delete_organization = AsyncMock(return_value=True)
    svc.get_org_health = AsyncMock(
        return_value={
            "health": "healthy",
            "active_missions": 3,
            "active_teams": 5,
            "pending_approvals": 0,
            "task_counts": {"queued": 2, "running": 3, "completed": 10, "blocked": 0, "failed": 0},
            "event_counts_24h": {"mission.created": 1},
            "items_needing_attention": 0,
        }
    )
    svc.create_department = AsyncMock(return_value=_fake_dept())
    svc.list_departments = AsyncMock(return_value=[_fake_dept()])
    svc.get_department = AsyncMock(return_value=_fake_dept())
    svc.update_department = AsyncMock(return_value=_fake_dept())
    svc.create_mission = AsyncMock(return_value=_fake_mission())
    svc.list_missions = AsyncMock(return_value=[_fake_mission()])
    svc.get_mission = AsyncMock(return_value=_fake_mission())
    svc.update_mission_status = AsyncMock(return_value=_fake_mission(status="active"))
    svc.update_mission = AsyncMock(return_value=_fake_mission(title="Updated title"))
    svc.get_mission_timeline = AsyncMock(return_value={"phases": []})
    svc.create_task = AsyncMock(return_value=_fake_task())
    svc.list_tasks = AsyncMock(return_value=[_fake_task()])
    svc.get_task = AsyncMock(return_value=_fake_task())
    svc.update_task_status = AsyncMock(return_value=_fake_task(status="running"))
    svc.create_team = AsyncMock(return_value=_fake_team())
    svc.list_teams = AsyncMock(return_value=[_fake_team()])
    svc.get_team = AsyncMock(return_value=_fake_team())
    svc.update_team = AsyncMock(return_value=_fake_team())
    svc.get_team_member_profiles = AsyncMock(return_value=[])
    svc.list_events = AsyncMock(return_value=[])
    svc.get_agent_audit = AsyncMock(return_value=[])
    svc.dispatch_mission_goal = AsyncMock(return_value={"dispatched": True, "goal_id": "g-1"})
    svc.create_mission_schedule = AsyncMock()
    svc.list_mission_schedules = AsyncMock(return_value=[])
    svc.set_mission_schedule_enabled = AsyncMock(return_value=None)
    svc.delete_mission_schedule = AsyncMock(return_value=True)
    svc.approve_schedule_publishing = AsyncMock(return_value=None)
    svc.release_pending_publish_missions = AsyncMock(return_value=[])
    svc.finalize_mission = AsyncMock(return_value={"finalized": True})
    return svc


@pytest.fixture
async def client(test_app: FastAPI, mock_service: MagicMock) -> AsyncClient:
    from app.org.router import get_org_service

    async def _override():
        yield mock_service

    test_app.dependency_overrides[get_org_service] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as c:
            yield c
    finally:
        test_app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# Organization: update/delete not-found paths
# ══════════════════════════════════════════════════════════════════════════════


class TestOrgNotFoundPaths:
    async def test_update_org_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.update_organization = AsyncMock(return_value=None)
        r = await client.patch(f"/v1/org/{ORG_ID}", json={"name": "New name"})
        assert r.status_code == 404

    async def test_delete_org_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.delete_organization = AsyncMock(return_value=False)
        r = await client.delete(f"/v1/org/{ORG_ID}")
        assert r.status_code == 404

    async def test_delete_org_success_204(self, client: AsyncClient, mock_service: MagicMock) -> None:
        r = await client.delete(f"/v1/org/{ORG_ID}")
        assert r.status_code == 204


class TestOrgAutonomy:
    async def test_get_autonomy_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/autonomy")
        assert r.status_code == 404

    async def test_get_autonomy_ok(self, client: AsyncClient) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/autonomy")
        assert r.status_code == 200
        assert "autonomy_level" in r.json()

    async def test_update_autonomy_level_out_of_range(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        r = await client.patch(f"/v1/org/{ORG_ID}/autonomy", json={"autonomy_level": 9})
        assert r.status_code == 422

    async def test_update_autonomy_org_not_found_initial(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.patch(f"/v1/org/{ORG_ID}/autonomy", json={"autonomy_level": 3})
        assert r.status_code == 404

    async def test_update_autonomy_then_disappears(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        # get_organization succeeds first, then update_organization returns None —
        # covers the re-fetch-not-found branch inside update_org_autonomy.
        mock_service.update_organization = AsyncMock(return_value=None)
        r = await client.patch(f"/v1/org/{ORG_ID}/autonomy", json={"autonomy_level": 3})
        assert r.status_code == 404

    async def test_update_autonomy_settings_merge(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        org = _fake_org()
        org.settings = {"autonomy": {"existing": True}, "other": "keep"}
        mock_service.get_organization = AsyncMock(return_value=org)
        mock_service.update_organization = AsyncMock(return_value=org)
        r = await client.patch(
            f"/v1/org/{ORG_ID}/autonomy", json={"settings": {"new_key": "val"}}
        )
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Departments
# ══════════════════════════════════════════════════════════════════════════════


class TestDepartmentNotFound:
    async def test_get_department_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_department = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/departments/{DEPT_ID}")
        assert r.status_code == 404

    async def test_update_department_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.update_department = AsyncMock(return_value=None)
        r = await client.patch(
            f"/v1/org/{ORG_ID}/departments/{DEPT_ID}", json={"name": "New"}
        )
        assert r.status_code == 404

    async def test_update_department_ok(self, client: AsyncClient) -> None:
        r = await client.patch(
            f"/v1/org/{ORG_ID}/departments/{DEPT_ID}", json={"name": "New"}
        )
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Missions: get/update/status/timeline — not found + cross-org scoping
# ══════════════════════════════════════════════════════════════════════════════


class TestMissionScoping:
    async def test_get_mission_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_mission = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/missions/{MISSION_ID}")
        assert r.status_code == 404

    async def test_get_mission_cross_org_404(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(org_id=OTHER_ORG_ID))
        r = await client.get(f"/v1/org/{ORG_ID}/missions/{MISSION_ID}")
        assert r.status_code == 404

    async def test_update_mission_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_mission = AsyncMock(return_value=None)
        r = await client.patch(
            f"/v1/org/{ORG_ID}/missions/{MISSION_ID}", json={"title": "New"}
        )
        assert r.status_code == 404

    async def test_update_mission_cross_org(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(org_id=OTHER_ORG_ID))
        r = await client.patch(
            f"/v1/org/{ORG_ID}/missions/{MISSION_ID}", json={"title": "New"}
        )
        assert r.status_code == 404

    async def test_update_mission_via_status_field(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        r = await client.patch(
            f"/v1/org/{ORG_ID}/missions/{MISSION_ID}", json={"status": "active"}
        )
        assert r.status_code == 200
        mock_service.update_mission_status.assert_awaited()

    async def test_update_mission_no_updates_returns_existing(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        r = await client.patch(f"/v1/org/{ORG_ID}/missions/{MISSION_ID}", json={})
        assert r.status_code == 200

    async def test_update_mission_result_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.update_mission = AsyncMock(return_value=None)
        r = await client.patch(
            f"/v1/org/{ORG_ID}/missions/{MISSION_ID}", json={"title": "x"}
        )
        assert r.status_code == 404

    async def test_status_update_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_mission = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/status", json={"status": "active"}
        )
        assert r.status_code == 404

    async def test_status_update_cross_org(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(org_id=OTHER_ORG_ID))
        r = await client.post(
            f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/status", json={"status": "active"}
        )
        assert r.status_code == 404

    async def test_status_update_value_error(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.update_mission_status = AsyncMock(side_effect=ValueError("bad transition"))
        r = await client.post(
            f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/status", json={"status": "bogus"}
        )
        assert r.status_code == 422

    async def test_status_update_result_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.update_mission_status = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/status", json={"status": "active"}
        )
        assert r.status_code == 404

    async def test_mission_timeline_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission_timeline = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/timeline")
        assert r.status_code == 404

    async def test_mission_timeline_ok(self, client: AsyncClient) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/timeline")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Brain decisions + proposal approve/reject
# ══════════════════════════════════════════════════════════════════════════════


class TestBrainProposals:
    async def test_list_brain_decisions(self, client: AsyncClient) -> None:
        with patch("app.org.router.BrainDecisionStore") as store_cls:
            store_cls.return_value.list = AsyncMock(return_value=[{"id": "d1"}])
            r = await client.get(f"/v1/org/{ORG_ID}/brain/decisions")
        assert r.status_code == 200
        assert r.json() == [{"id": "d1"}]

    async def test_approve_proposal_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=None)
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/approve")
        assert r.status_code == 404

    async def test_approve_proposal_cross_org(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(org_id=OTHER_ORG_ID))
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/approve")
        assert r.status_code == 404

    async def test_approve_proposal_not_proposed_status(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(status="active"))
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/approve")
        assert r.status_code == 409

    async def test_approve_proposal_ok(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(status="proposed"))
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/approve")
        assert r.status_code == 200

    async def test_reject_proposal_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=None)
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/reject")
        assert r.status_code == 404

    async def test_reject_proposal_cross_org(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(org_id=OTHER_ORG_ID))
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/reject")
        assert r.status_code == 404

    async def test_reject_proposal_not_proposed(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(status="active"))
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/reject")
        assert r.status_code == 409

    async def test_reject_proposal_ok(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(status="proposed"))
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/reject")
        assert r.status_code == 200
        assert r.json()["status"] == "active"  # from update_mission_status mock


# ══════════════════════════════════════════════════════════════════════════════
# Tasks
# ══════════════════════════════════════════════════════════════════════════════


class TestTaskEndpoints:
    async def test_create_task_ok(self, client: AsyncClient) -> None:
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks", json={"org_id": ORG_ID, "title": "Do the thing"}
        )
        assert r.status_code == 201

    async def test_create_task_value_error(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.create_task = AsyncMock(side_effect=ValueError("too deep"))
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks", json={"org_id": ORG_ID, "title": "Deep task", "depth": 8}
        )
        assert r.status_code == 422

    async def test_list_tasks_ok(self, client: AsyncClient) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/tasks")
        assert r.status_code == 200
        assert "data" in r.json()

    async def test_get_task_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_task = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/tasks/{TASK_ID}")
        assert r.status_code == 404

    async def test_get_task_cross_org(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_task = AsyncMock(return_value=_fake_task(org_id=OTHER_ORG_ID))
        r = await client.get(f"/v1/org/{ORG_ID}/tasks/{TASK_ID}")
        assert r.status_code == 404

    async def test_get_task_ok(self, client: AsyncClient) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/tasks/{TASK_ID}")
        assert r.status_code == 200

    async def test_update_task_status_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_task = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/status", json={"status": "running"}
        )
        assert r.status_code == 404

    async def test_update_task_status_cross_org(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_task = AsyncMock(return_value=_fake_task(org_id=OTHER_ORG_ID))
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/status", json={"status": "running"}
        )
        assert r.status_code == 404

    async def test_update_task_status_value_error(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.update_task_status = AsyncMock(side_effect=ValueError("bad status"))
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/status", json={"status": "bogus"}
        )
        assert r.status_code == 422

    async def test_update_task_status_result_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.update_task_status = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/status", json={"status": "running"}
        )
        assert r.status_code == 404

    async def test_update_task_status_ok(self, client: AsyncClient) -> None:
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/status", json={"status": "running"}
        )
        assert r.status_code == 200


class TestTaskApproval:
    async def test_approve_task_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_task = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/approve", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_approve_task_cross_org(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_task = AsyncMock(return_value=_fake_task(org_id=OTHER_ORG_ID))
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/approve", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_approve_task_result_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.update_task_status = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/approve", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_approve_task_ok_with_hitl_and_event(
        self, client: AsyncClient, mock_service: MagicMock, test_app: FastAPI
    ) -> None:
        # Task carries a paired HITL request id — exercises _extract_hitl_request_id
        # and _resolve_task_hitl_request's synchronous-approve path.
        mock_service.get_task = AsyncMock(
            return_value=_fake_task(outputs=[{"hitl_request_id": "req-1"}])
        )
        gateway = MagicMock()
        gateway.approve = MagicMock(return_value=True)  # sync return, not awaitable
        test_app.state.hitl_gateway = gateway
        with patch("app.org.events.get_org_event_publisher") as get_pub:
            pub = MagicMock()
            pub.publish = AsyncMock()
            get_pub.return_value = pub
            r = await client.post(
                f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/approve",
                json={"approver": "alice", "note": "looks good"},
            )
        assert r.status_code == 200
        pub.publish.assert_awaited()

    async def test_approve_task_hitl_gateway_awaitable_result(
        self, client: AsyncClient, mock_service: MagicMock, test_app: FastAPI
    ) -> None:
        mock_service.get_task = AsyncMock(
            return_value=_fake_task(extra_data={"approval_request_id": "req-2"})
        )
        gateway = MagicMock()

        async def _approve_result() -> bool:
            return True

        gateway.approve = MagicMock(return_value=_approve_result())
        test_app.state.hitl_gateway = gateway
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/approve", json={"approver": "bob"}
        )
        assert r.status_code == 200

    async def test_approve_task_event_publish_failure_is_swallowed(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        with patch("app.org.events.get_org_event_publisher", side_effect=RuntimeError("boom")):
            r = await client.post(
                f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/approve", json={"approver": "alice"}
            )
        assert r.status_code == 200

    async def test_reject_task_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_task = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/reject", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_reject_task_cross_org(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_task = AsyncMock(return_value=_fake_task(org_id=OTHER_ORG_ID))
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/reject", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_reject_task_result_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.update_task_status = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/reject", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_reject_task_ok_with_hitl_and_event(
        self, client: AsyncClient, mock_service: MagicMock, test_app: FastAPI
    ) -> None:
        mock_service.get_task = AsyncMock(
            return_value=_fake_task(outputs=[{"hitl_request_id": "req-3"}])
        )
        gateway = MagicMock()
        gateway.reject = AsyncMock(return_value=True)
        test_app.state.hitl_gateway = gateway
        with patch("app.org.events.get_org_event_publisher") as get_pub:
            pub = MagicMock()
            pub.publish = AsyncMock()
            get_pub.return_value = pub
            r = await client.post(
                f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/reject",
                json={"approver": "alice", "note": "nope"},
            )
        assert r.status_code == 200

    async def test_resolve_hitl_no_request_id_noop(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_task = AsyncMock(return_value=_fake_task(outputs=[]))
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/reject", json={"approver": "alice"}
        )
        assert r.status_code == 200

    async def test_resolve_hitl_no_gateway_noop(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_task = AsyncMock(
            return_value=_fake_task(outputs=[{"hitl_request_id": "req-4"}])
        )
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/reject", json={"approver": "alice"}
        )
        assert r.status_code == 200

    async def test_resolve_hitl_gateway_raises_is_swallowed(
        self, client: AsyncClient, mock_service: MagicMock, test_app: FastAPI
    ) -> None:
        mock_service.get_task = AsyncMock(
            return_value=_fake_task(outputs=[{"approval_request_id": "req-5"}])
        )
        gateway = MagicMock()
        gateway.reject = AsyncMock(side_effect=RuntimeError("gateway down"))
        test_app.state.hitl_gateway = gateway
        r = await client.post(
            f"/v1/org/{ORG_ID}/tasks/{TASK_ID}/reject", json={"approver": "alice"}
        )
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Teams
# ══════════════════════════════════════════════════════════════════════════════


class TestTeamEndpoints:
    async def test_create_team_ok(self, client: AsyncClient) -> None:
        r = await client.post(
            f"/v1/org/{ORG_ID}/teams", json={"org_id": ORG_ID, "name": "Squad"}
        )
        assert r.status_code == 201

    async def test_list_teams_ok(self, client: AsyncClient) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/teams")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_teams_with_dept_filter(self, client: AsyncClient, mock_service: MagicMock) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/teams?dept_id={DEPT_ID}")
        assert r.status_code == 200
        mock_service.list_teams.assert_awaited_with(ORG_ID, dept_id=DEPT_ID)

    async def test_list_team_members_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_team = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/teams/{TEAM_ID}/members")
        assert r.status_code == 404

    async def test_list_team_members_ok(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_team = AsyncMock(return_value=_fake_team(member_agent_ids=["a1"]))
        mock_service.get_team_member_profiles = AsyncMock(return_value=[{"id": "a1"}])
        r = await client.get(f"/v1/org/{ORG_ID}/teams/{TEAM_ID}/members")
        assert r.status_code == 200
        data = r.json()
        assert data["member_ids"] == ["a1"]
        assert data["members"] == [{"id": "a1"}]


# ══════════════════════════════════════════════════════════════════════════════
# Events / Audit
# ══════════════════════════════════════════════════════════════════════════════


class TestEventsAudit:
    async def test_list_events_ok(self, client: AsyncClient) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/events")
        assert r.status_code == 200
        assert "data" in r.json()

    async def test_get_agent_audit_ok(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_agent_audit = AsyncMock(return_value=[{"event": "task.completed"}])
        r = await client.get(f"/v1/org/{ORG_ID}/agents/agent-1/audit")
        assert r.status_code == 200
        assert r.json() == [{"event": "task.completed"}]


# ══════════════════════════════════════════════════════════════════════════════
# Org-scoped approvals list + approve/reject
# ══════════════════════════════════════════════════════════════════════════════


class TestOrgApprovalsList:
    async def test_list_approvals_pending_default(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        gate = _fake_task(
            status="approval_required",
            extra_data={"task_kind": "approval_gate", "gate": {"type": "publish"}},
            cost_estimate_usd=1.5,
        )
        mock_service.list_tasks = AsyncMock(return_value=[gate])
        mock_service.list_missions = AsyncMock(return_value=[_fake_mission(status="active")])
        r = await client.get(f"/v1/org/{ORG_ID}/approvals")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["data"][0]["status"] == "pending"

    async def test_list_approvals_resolved_filter(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        gate = _fake_task(
            status="running",
            extra_data={"task_kind": "approval_gate"},
            outputs=[{"approved_by": "alice", "approval_note": "ok"}],
        )
        mock_service.list_tasks = AsyncMock(return_value=[gate])
        r = await client.get(f"/v1/org/{ORG_ID}/approvals?status=resolved")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["data"][0]["approver"] == "alice"

    async def test_list_approvals_excludes_closed_mission(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        gate = _fake_task(
            status="approval_required", extra_data={"task_kind": "approval_gate"}
        )
        mock_service.list_tasks = AsyncMock(return_value=[gate])
        mock_service.list_missions = AsyncMock(
            return_value=[_fake_mission(status="completed")]
        )
        r = await client.get(f"/v1/org/{ORG_ID}/approvals")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    async def test_list_approvals_list_missions_raises_falls_back_open(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        gate = _fake_task(
            status="approval_required", extra_data={"task_kind": "approval_gate"}
        )
        gate.mission_id = None
        mock_service.list_tasks = AsyncMock(return_value=[gate])
        mock_service.list_missions = AsyncMock(side_effect=RuntimeError("db down"))
        r = await client.get(f"/v1/org/{ORG_ID}/approvals")
        assert r.status_code == 200
        # mission_id is None on the gate -> stays selected regardless of the
        # (empty, because list_missions raised) open-missions fallback set.
        assert r.json()["total"] == 1

    async def test_list_approvals_list_tasks_raises_returns_error_payload(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.list_tasks = AsyncMock(side_effect=RuntimeError("db down"))
        r = await client.get(f"/v1/org/{ORG_ID}/approvals")
        assert r.status_code == 200
        data = r.json()
        assert data["data"] == []
        assert data["total"] == 0
        assert "error" in data


class TestOrgApprovalDecision:
    async def test_approve_request_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_task = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/approve", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_approve_request_wrong_task_kind(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_task = AsyncMock(return_value=_fake_task(extra_data={}))
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/approve", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_approve_request_cross_org(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_task = AsyncMock(
            return_value=_fake_task(
                org_id=OTHER_ORG_ID, extra_data={"task_kind": "approval_gate"}
            )
        )
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/approve", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_approve_request_updated_none(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_task = AsyncMock(
            return_value=_fake_task(extra_data={"task_kind": "approval_gate"})
        )
        mock_service.update_task_status = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/approve", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_approve_request_ok_dispatches_last_gate(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        gate = _fake_task(extra_data={"task_kind": "approval_gate"})
        mock_service.get_task = AsyncMock(return_value=gate)
        mock_service.update_task_status = AsyncMock(return_value=_fake_task(status="running"))
        mock_service.list_tasks = AsyncMock(return_value=[])  # no remaining gates
        mock_service.dispatch_mission_goal = AsyncMock(
            return_value={"dispatched": True, "goal_id": "g-1"}
        )
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/approve",
            json={"approver": "alice", "notes": "go ahead"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["mission_dispatched"] is True
        assert data["goal_id"] == "g-1"

    async def test_approve_request_ok_gates_remaining_no_dispatch(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        gate = _fake_task(extra_data={"task_kind": "approval_gate"})
        other_gate = _fake_task(
            extra_data={"task_kind": "approval_gate"}, status="approval_required"
        )
        mock_service.get_task = AsyncMock(return_value=gate)
        mock_service.update_task_status = AsyncMock(return_value=_fake_task(status="running"))
        mock_service.list_tasks = AsyncMock(return_value=[other_gate])
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/approve", json={"approver": "alice"}
        )
        assert r.status_code == 200
        assert r.json()["mission_dispatched"] is False

    async def test_reject_request_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_task = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/reject", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_reject_request_updated_none(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_task = AsyncMock(
            return_value=_fake_task(extra_data={"task_kind": "approval_gate"})
        )
        mock_service.update_task_status = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/reject", json={"approver": "alice"}
        )
        assert r.status_code == 404

    async def test_reject_request_ok_fails_open_mission(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        gate = _fake_task(extra_data={"task_kind": "approval_gate"})
        mock_service.get_task = AsyncMock(return_value=gate)
        mock_service.update_task_status = AsyncMock(return_value=_fake_task(status="cancelled"))
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(status="active"))
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/reject",
            json={"approver": "alice", "note": "denied"},
        )
        assert r.status_code == 200
        mock_service.update_mission_status.assert_awaited_with(str(gate.mission_id), "failed")

    async def test_reject_request_mission_already_closed_no_fail(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        gate = _fake_task(extra_data={"task_kind": "approval_gate"})
        mock_service.get_task = AsyncMock(return_value=gate)
        mock_service.update_task_status = AsyncMock(return_value=_fake_task(status="cancelled"))
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(status="completed"))
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/reject", json={"approver": "alice"}
        )
        assert r.status_code == 200

    async def test_reject_request_get_mission_raises_is_swallowed(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        gate = _fake_task(extra_data={"task_kind": "approval_gate"})
        mock_service.get_task = AsyncMock(return_value=gate)
        mock_service.update_task_status = AsyncMock(return_value=_fake_task(status="cancelled"))
        mock_service.get_mission = AsyncMock(side_effect=RuntimeError("db down"))
        r = await client.post(
            f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/reject", json={"approver": "alice"}
        )
        assert r.status_code == 200

    async def test_reject_request_event_publish_failure_swallowed(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        gate = _fake_task(extra_data={"task_kind": "approval_gate"})
        mock_service.get_task = AsyncMock(return_value=gate)
        mock_service.update_task_status = AsyncMock(return_value=_fake_task(status="cancelled"))
        mock_service.get_mission = AsyncMock(return_value=None)
        with patch("app.org.events.get_org_event_publisher", side_effect=RuntimeError("boom")):
            r = await client.post(
                f"/v1/org/{ORG_ID}/approvals/{TASK_ID}/reject", json={"approver": "alice"}
            )
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# RBAC role management
# ══════════════════════════════════════════════════════════════════════════════


class TestOrgRoles:
    async def test_list_roles_built_in_only(self, client: AsyncClient) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/roles")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 5
        assert all(role["is_built_in"] for role in data)

    async def test_create_and_list_custom_role(self, client: AsyncClient) -> None:
        r = await client.post(
            f"/v1/org/{ORG_ID}/roles",
            json={"name": "Reviewer", "description": "Reviews stuff", "permissions": []},
        )
        assert r.status_code == 201
        role = r.json()
        assert role["name"] == "Reviewer"
        assert role["is_built_in"] is False

        r2 = await client.get(f"/v1/org/{ORG_ID}/roles")
        assert len(r2.json()) == 6

        # update it
        r3 = await client.put(
            f"/v1/org/{ORG_ID}/roles/{role['id']}",
            json={"name": "Reviewer v2", "description": "", "permissions": []},
        )
        assert r3.status_code == 200
        assert r3.json()["name"] == "Reviewer v2"

        # delete it
        r4 = await client.delete(f"/v1/org/{ORG_ID}/roles/{role['id']}")
        assert r4.status_code == 204

    async def test_update_role_not_found(self, client: AsyncClient) -> None:
        r = await client.put(
            f"/v1/org/{ORG_ID}/roles/does-not-exist",
            json={"name": "X", "description": "", "permissions": []},
        )
        assert r.status_code == 404

    async def test_delete_role_not_found(self, client: AsyncClient) -> None:
        r = await client.delete(f"/v1/org/{ORG_ID}/roles/does-not-exist")
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# Emergency stop / resume
# ══════════════════════════════════════════════════════════════════════════════


class TestEmergencyStop:
    async def test_emergency_stop_no_redis(self, client: AsyncClient) -> None:
        r = await client.post(f"/v1/org/{ORG_ID}/emergency-stop")
        assert r.status_code == 200
        assert r.json()["status"] == "stopped"

    async def test_emergency_stop_with_redis(self, client: AsyncClient, test_app: FastAPI) -> None:
        redis = MagicMock()
        redis.set = AsyncMock()
        test_app.state._redis = redis
        r = await client.post(f"/v1/org/{ORG_ID}/emergency-stop")
        assert r.status_code == 200
        redis.set.assert_awaited()

    async def test_emergency_resume_no_redis(self, client: AsyncClient) -> None:
        r = await client.post(f"/v1/org/{ORG_ID}/emergency-stop/resume")
        assert r.status_code == 200
        assert r.json()["status"] == "resumed"

    async def test_emergency_resume_with_redis(self, client: AsyncClient, test_app: FastAPI) -> None:
        redis = MagicMock()
        redis.delete = AsyncMock()
        test_app.state._redis = redis
        r = await client.post(f"/v1/org/{ORG_ID}/emergency-stop/resume")
        assert r.status_code == 200
        redis.delete.assert_awaited()


# ══════════════════════════════════════════════════════════════════════════════
# Morning brief / universal command
# ══════════════════════════════════════════════════════════════════════════════


class TestMorningBriefAndCommand:
    async def test_morning_brief_org_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/brief/morning")
        assert r.status_code == 404

    async def test_morning_brief_ok_with_priorities_and_risks(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_org_health = AsyncMock(
            return_value={
                "health": "degraded",
                "active_missions": 2,
                "active_teams": 1,
                "pending_approvals": 1,
                "task_counts": {"blocked": 1, "failed": 2},
                "event_counts_24h": {},
                "items_needing_attention": 1,
            }
        )
        r = await client.get(f"/v1/org/{ORG_ID}/brief/morning")
        assert r.status_code == 200
        data = r.json()
        assert len(data["priorities"]) >= 2
        assert len(data["risks"]) == 1

    async def test_universal_command_org_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/command", json={"command": "list missions"}
        )
        assert r.status_code == 404

    async def test_universal_command_high_risk_requires_2fa(self, client: AsyncClient) -> None:
        r = await client.post(
            f"/v1/org/{ORG_ID}/command", json={"command": "please delete everything"}
        )
        assert r.status_code == 202
        data = r.json()
        assert data["requires_2fa"] is True

    async def test_universal_command_low_risk_routes_to_agent(
        self, client: AsyncClient, test_app: FastAPI
    ) -> None:
        goal_service = MagicMock()
        goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g-42"})
        test_app.state.goal_service = goal_service
        r = await client.post(
            f"/v1/org/{ORG_ID}/command", json={"command": "summarize this week"}
        )
        assert r.status_code == 202
        assert r.json()["requires_2fa"] is False

    async def test_list_commands_and_get_command(
        self, client: AsyncClient, test_app: FastAPI
    ) -> None:
        import sys

        router_mod = sys.modules["app.org.router"]

        router_mod._COMMAND_HISTORY[ORG_ID] = [
            {"command_id": "c1", "channel": "rest", "status": "queued"},
            {"command_id": "c2", "channel": "telegram", "status": "routed"},
        ]
        r = await client.get(f"/v1/org/{ORG_ID}/commands")
        assert r.status_code == 200
        assert r.json()["total"] == 2

        r2 = await client.get(f"/v1/org/{ORG_ID}/commands?channel=telegram")
        assert r2.status_code == 200
        assert len(r2.json()["commands"]) == 1

        r3 = await client.get(f"/v1/org/{ORG_ID}/commands/c1")
        assert r3.status_code == 200
        assert r3.json()["command_id"] == "c1"

        r4 = await client.get(f"/v1/org/{ORG_ID}/commands/does-not-exist")
        assert r4.status_code == 404

        router_mod._COMMAND_HISTORY.pop(ORG_ID, None)


# ══════════════════════════════════════════════════════════════════════════════
# Team lifecycle
# ══════════════════════════════════════════════════════════════════════════════


class TestTeamLifecycle:
    async def test_transition_team_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_team = AsyncMock(return_value=None)
        r = await client.post(f"/v1/org/{ORG_ID}/teams/{TEAM_ID}/lifecycle")
        assert r.status_code == 404

    @pytest.mark.filterwarnings("ignore::starlette.exceptions.StarletteDeprecationWarning")
    @pytest.mark.filterwarnings("ignore:.*HTTP_422_UNPROCESSABLE_ENTITY.*:DeprecationWarning")
    async def test_transition_terminal_state_422(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        import warnings

        mock_service.get_team = AsyncMock(
            return_value=_fake_team(extra_data={"lifecycle_state": "archive"})
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = await client.post(f"/v1/org/{ORG_ID}/teams/{TEAM_ID}/lifecycle")
        assert r.status_code == 422

    async def test_transition_ok(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_team = AsyncMock(
            return_value=_fake_team(extra_data={"lifecycle_state": "create"})
        )
        r = await client.post(f"/v1/org/{ORG_ID}/teams/{TEAM_ID}/lifecycle")
        assert r.status_code == 200
        assert r.json()["current_state"] == "staff"

    async def test_get_lifecycle_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_team = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/teams/{TEAM_ID}/lifecycle")
        assert r.status_code == 404

    async def test_get_lifecycle_ok(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_team = AsyncMock(
            return_value=_fake_team(extra_data={"lifecycle_state": "brief"})
        )
        r = await client.get(f"/v1/org/{ORG_ID}/teams/{TEAM_ID}/lifecycle")
        assert r.status_code == 200
        assert r.json()["current_state"] == "brief"


# ══════════════════════════════════════════════════════════════════════════════
# Digital twin: not-found path
# ══════════════════════════════════════════════════════════════════════════════


class TestDigitalTwinNotFound:
    async def test_simulate_org_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/twin/simulate", json={"title": "New mission"}
        )
        assert r.status_code == 404


class TestStrategicBriefNotFound:
    async def test_strategic_brief_org_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/brief/strategic")
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# Mission attachments (upload)
# ══════════════════════════════════════════════════════════════════════════════


class TestUploadAttachment:
    async def test_upload_unsupported_type(self, client: AsyncClient) -> None:
        r = await client.post(
            f"/v1/org/{ORG_ID}/attachments",
            files={"file": ("evil.exe", b"MZ...", "application/x-msdownload")},
        )
        assert r.status_code == 415

    async def test_upload_empty_file(self, client: AsyncClient) -> None:
        r = await client.post(
            f"/v1/org/{ORG_ID}/attachments",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert r.status_code == 400

    @pytest.mark.filterwarnings("ignore::starlette.exceptions.StarletteDeprecationWarning")
    @pytest.mark.filterwarnings(
        "ignore:.*HTTP_413_REQUEST_ENTITY_TOO_LARGE.*:DeprecationWarning"
    )
    async def test_upload_too_large(self, client: AsyncClient) -> None:
        import warnings

        with (
            patch("app.org.router._ATTACHMENT_MAX_BYTES", 10),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore")
            r = await client.post(
                f"/v1/org/{ORG_ID}/attachments",
                files={"file": ("big.txt", b"x" * 20, "text/plain")},
            )
        assert r.status_code == 413

    async def test_upload_ok(self, client: AsyncClient, tmp_path) -> None:
        with patch.dict("os.environ", {"ORG_ATTACHMENTS_DIR": str(tmp_path)}):
            r = await client.post(
                f"/v1/org/{ORG_ID}/attachments",
                files={"file": ("notes.txt", b"hello world", "text/plain")},
            )
        assert r.status_code == 201
        data = r.json()
        assert data["filename"] == "notes.txt"
        assert data["size"] == len(b"hello world")


# ══════════════════════════════════════════════════════════════════════════════
# Mission preview (no DB dependency override needed — no get_org_service dep)
# ══════════════════════════════════════════════════════════════════════════════


class TestMissionPreview:
    async def test_preview_mission_ok(self, client: AsyncClient) -> None:
        with patch("app.org.loop_detector.OrgSimulationEngine") as engine_cls:
            estimate = MagicMock()
            estimate.departments_needed = ["Engineering"]
            estimate.estimated_agents = 3
            estimate.estimated_duration_hours = 5.0
            estimate.estimated_cost_usd = 12.0
            estimate.estimated_risk = "low"
            estimate.confidence = 0.8
            estimate.success_probability = 0.9
            estimate.potential_blockers = []
            engine_cls.return_value.estimate_mission = AsyncMock(return_value=estimate)
            r = await client.post(
                f"/v1/org/{ORG_ID}/missions/preview", json={"goal": "Launch a product"}
            )
        assert r.status_code == 200
        assert r.json()["estimated_agents"] == 3

    async def test_preview_mission_unauthenticated(self) -> None:
        app = FastAPI()
        app.include_router(org_router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"/v1/org/{ORG_ID}/missions/preview", json={"goal": "x"})
        assert r.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# Schedules
# ══════════════════════════════════════════════════════════════════════════════


def _fake_schedule(**kw: Any) -> MagicMock:
    m = MagicMock()
    m.id = uuid.uuid4()
    m.org_id = uuid.UUID(ORG_ID)
    m.name = kw.get("name", "Nightly")
    m.title = "Nightly digest"
    m.objective = "Summarize the day"
    m.priority = "medium"
    m.autonomy_level = 1
    m.cron_expression = "0 0 * * *"
    m.timezone = "UTC"
    m.enabled = kw.get("enabled", True)
    m.next_fire_at = None
    m.last_fired_at = None
    m.last_mission_id = None
    m.fire_count = 0
    m.created_at = NOW
    m.publish_config = kw.get("publish_config")
    return m


class TestSchedules:
    @pytest.mark.filterwarnings("ignore::starlette.exceptions.StarletteDeprecationWarning")
    @pytest.mark.filterwarnings("ignore:.*HTTP_422_UNPROCESSABLE_ENTITY.*:DeprecationWarning")
    async def test_create_schedule_invalid_cron(self, client: AsyncClient) -> None:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = await client.post(
                f"/v1/org/{ORG_ID}/schedules",
                json={"title": "Nightly", "cron_expression": "not-a-cron"},
            )
        assert r.status_code == 422

    async def test_create_schedule_org_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/schedules",
            json={"title": "Nightly", "cron_expression": "0 0 * * *"},
        )
        assert r.status_code == 404

    async def test_create_schedule_ok_no_publish(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.create_mission_schedule = AsyncMock(return_value=_fake_schedule())
        r = await client.post(
            f"/v1/org/{ORG_ID}/schedules",
            json={"title": "Nightly", "cron_expression": "0 0 * * *"},
        )
        assert r.status_code == 201
        assert r.json()["publish"] is None

    async def test_create_schedule_ok_with_publish_starts_unapproved(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        created: dict[str, Any] = {}

        async def _create(**kwargs: Any) -> MagicMock:
            created.update(kwargs)
            return _fake_schedule(publish_config=kwargs.get("publish_config"))

        mock_service.create_mission_schedule = AsyncMock(side_effect=_create)
        r = await client.post(
            f"/v1/org/{ORG_ID}/schedules",
            json={
                "title": "Nightly",
                "cron_expression": "0 0 * * *",
                "publish": {
                    "connector_server_id": "conn-1",
                    "tool_name": "send_email",
                    "arguments": {"to": "{{deliverable}}"},
                },
            },
        )
        assert r.status_code == 201
        assert created["publish_config"]["approved"] is False
        assert r.json()["publish"]["approved"] is False

    async def test_list_schedules(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.list_mission_schedules = AsyncMock(return_value=[_fake_schedule()])
        r = await client.get(f"/v1/org/{ORG_ID}/schedules")
        assert r.status_code == 200
        assert len(r.json()) == 1

    async def test_toggle_schedule_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.set_mission_schedule_enabled = AsyncMock(return_value=None)
        r = await client.patch(
            f"/v1/org/{ORG_ID}/schedules/sched-1", json={"enabled": False}
        )
        assert r.status_code == 404

    async def test_toggle_schedule_ok(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.set_mission_schedule_enabled = AsyncMock(
            return_value=_fake_schedule(enabled=False)
        )
        r = await client.patch(
            f"/v1/org/{ORG_ID}/schedules/sched-1", json={"enabled": False}
        )
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    async def test_delete_schedule_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.delete_mission_schedule = AsyncMock(return_value=False)
        r = await client.delete(f"/v1/org/{ORG_ID}/schedules/sched-1")
        assert r.status_code == 404

    async def test_delete_schedule_ok(self, client: AsyncClient) -> None:
        r = await client.delete(f"/v1/org/{ORG_ID}/schedules/sched-1")
        assert r.status_code == 204

    async def test_approve_publishing_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.approve_schedule_publishing = AsyncMock(return_value=None)
        r = await client.post(
            f"/v1/org/{ORG_ID}/schedules/sched-1/approve-publishing", json={"approved": True}
        )
        assert r.status_code == 404

    async def test_approve_publishing_releases_pending_missions(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.approve_schedule_publishing = AsyncMock(return_value=_fake_schedule())
        mock_service.release_pending_publish_missions = AsyncMock(return_value=["m-1", "m-2"])
        with patch("app.scaling.tasks.publish_mission_deliverable") as task:
            task.apply_async = MagicMock()
            r = await client.post(
                f"/v1/org/{ORG_ID}/schedules/sched-1/approve-publishing",
                json={"approved": True},
            )
        assert r.status_code == 200
        assert r.json()["released_missions"] == ["m-1", "m-2"]
        assert task.apply_async.call_count == 2

    async def test_revoke_publishing_no_release(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.approve_schedule_publishing = AsyncMock(return_value=_fake_schedule())
        r = await client.post(
            f"/v1/org/{ORG_ID}/schedules/sched-1/approve-publishing",
            json={"approved": False},
        )
        assert r.status_code == 200
        assert r.json()["released_missions"] == []
        mock_service.release_pending_publish_missions.assert_not_awaited()


# ══════════════════════════════════════════════════════════════════════════════
# Mission finalize
# ══════════════════════════════════════════════════════════════════════════════


class TestFinalizeMission:
    async def test_finalize_not_found(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_mission = AsyncMock(return_value=None)
        r = await client.post(f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/finalize")
        assert r.status_code == 404

    async def test_finalize_cross_org(self, client: AsyncClient, mock_service: MagicMock) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(org_id=OTHER_ORG_ID))
        r = await client.post(f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/finalize")
        assert r.status_code == 404

    async def test_finalize_ok(self, client: AsyncClient, mock_service: MagicMock) -> None:
        r = await client.post(f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/finalize")
        assert r.status_code == 200
        assert r.json()["finalized"] is True


# ══════════════════════════════════════════════════════════════════════════════
# get_org_service dependency internals (not exercised by the dependency-override
# fixtures above — call it directly as an async generator).
# ══════════════════════════════════════════════════════════════════════════════


class TestGetOrgServiceDependency:
    async def test_missing_tenant_raises_401(self) -> None:
        from app.org.router import get_org_service

        request = MagicMock()
        request.state = MagicMock()
        request.state.tenant = None

        gen = get_org_service(request)
        with pytest.raises(Exception) as exc_info:
            await gen.__anext__()
        assert getattr(exc_info.value, "status_code", None) == 401

    async def test_missing_session_factory_raises_503(self) -> None:
        from app.org.router import get_org_service
        from app.tenancy.context import PlanTier, TenantContext

        request = MagicMock()
        request.state.tenant = TenantContext(
            tenant_id=TENANT_ID, plan=PlanTier.PROFESSIONAL, api_key_id="k"
        )
        request.app.state = MagicMock(spec=[])  # no db_session_factory attribute

        gen = get_org_service(request)
        with pytest.raises(Exception) as exc_info:
            await gen.__anext__()
        assert getattr(exc_info.value, "status_code", None) == 503

    async def test_missing_tenant_id_on_context_raises_500(self) -> None:
        from app.org.router import get_org_service

        request = MagicMock()
        tenant = MagicMock()
        tenant.tenant_id = None
        tenant.id = None
        request.state.tenant = tenant

        gen = get_org_service(request)
        with pytest.raises(Exception) as exc_info:
            await gen.__anext__()
        assert getattr(exc_info.value, "status_code", None) == 500

    async def test_yields_org_service_with_real_session_factory(self) -> None:
        from app.org.router import get_org_service
        from app.tenancy.context import PlanTier, TenantContext

        request = MagicMock()
        request.state.tenant = TenantContext(
            tenant_id=TENANT_ID, plan=PlanTier.PROFESSIONAL, api_key_id="k"
        )

        session = AsyncMock()
        session.begin = MagicMock()
        session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
        session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        @contextlib_asynccontextmanager
        async def _session_factory():
            yield session

        request.app.state.db_session_factory = lambda: _session_factory()

        with patch("app.db.rls.sqlalchemy_rls_context") as rls_ctx:
            rls_ctx.return_value.__aenter__ = AsyncMock(return_value=None)
            rls_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            gen = get_org_service(request)
            svc = await gen.__anext__()
            assert isinstance(svc, OrgService)
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()


# ══════════════════════════════════════════════════════════════════════════════
# _validate_uuid helper
# ══════════════════════════════════════════════════════════════════════════════


class TestValidateUuid:
    def test_valid_uuid_noop(self) -> None:
        from app.org.router import _validate_uuid

        _validate_uuid(str(uuid.uuid4()), "org_id")  # does not raise

    def test_invalid_uuid_raises_422(self) -> None:
        from app.org.router import _validate_uuid

        with pytest.raises(Exception) as exc_info:
            _validate_uuid("not-a-uuid", "org_id", "req-1")
        assert getattr(exc_info.value, "status_code", None) == 422


# ══════════════════════════════════════════════════════════════════════════════
# MCP WebSocket endpoint
# ══════════════════════════════════════════════════════════════════════════════


def _ws_app() -> FastAPI:
    app = FastAPI()
    app.include_router(org_router)
    return app


class TestMcpWebSocket:
    def test_initialize_tools_list_and_ping(self) -> None:
        client = TestClient(_ws_app())
        with client.websocket_connect(f"/v1/org/{ORG_ID}/mcp") as ws:
            ws.send_json({"id": 1, "method": "initialize"})
            resp = ws.receive_json()
            assert resp["id"] == 1
            assert "protocolVersion" in resp["result"]

            ws.send_json({"id": 2, "method": "tools/list"})
            resp = ws.receive_json()
            assert "tools" in resp["result"]

            ws.send_json({"id": 3, "method": "ping"})
            resp = ws.receive_json()
            assert resp["result"] == {}

            ws.send_json({"id": 4, "method": "notifications/initialized"})
            resp = ws.receive_json()
            assert resp["result"] == {}

    def test_unknown_method_returns_error(self) -> None:
        client = TestClient(_ws_app())
        with client.websocket_connect(f"/v1/org/{ORG_ID}/mcp") as ws:
            ws.send_json({"id": 1, "method": "bogus/method"})
            resp = ws.receive_json()
            assert resp["result"]["error"]["code"] == -32601

    def test_invalid_json_returns_parse_error(self) -> None:
        client = TestClient(_ws_app())
        with client.websocket_connect(f"/v1/org/{ORG_ID}/mcp") as ws:
            ws.send_text("not json{{{")
            resp = ws.receive_json()
            assert resp["error"]["code"] == -32700

    def test_tools_call_unknown_tool(self) -> None:
        client = TestClient(_ws_app())
        with client.websocket_connect(f"/v1/org/{ORG_ID}/mcp") as ws:
            ws.send_json(
                {"id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
            )
            resp = ws.receive_json()
            assert "result" in resp

    def test_resources_and_prompts_list_fallback(self) -> None:
        client = TestClient(_ws_app())
        with client.websocket_connect(f"/v1/org/{ORG_ID}/mcp?api_key=abc") as ws:
            ws.send_json({"id": 6, "method": "resources/list"})
            resp = ws.receive_json()
            assert "resources" in resp["result"]

            ws.send_json({"id": 7, "method": "prompts/list"})
            resp = ws.receive_json()
            assert "prompts" in resp["result"]

    def test_bearer_auth_header(self) -> None:
        client = TestClient(_ws_app())
        with client.websocket_connect(
            f"/v1/org/{ORG_ID}/mcp", headers={"Authorization": "Bearer secret-token"}
        ) as ws:
            ws.send_json({"id": 1, "method": "ping"})
            resp = ws.receive_json()
            assert resp["result"] == {}


# ══════════════════════════════════════════════════════════════════════════════
# SSE streams — org events, mission progress
# ══════════════════════════════════════════════════════════════════════════════


class _FakePubSub:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages

    async def __aenter__(self) -> _FakePubSub:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def subscribe(self, channel: str) -> None:
        return None

    async def unsubscribe(self, channel: str) -> None:
        return None

    async def listen(self):
        for msg in self._messages:
            yield msg


class _FakeRedis:
    def __init__(self, messages: list[dict[str, Any]], get_value: Any = None) -> None:
        self._messages = messages
        self._get_value = get_value

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._messages)

    async def get(self, key: str) -> Any:
        return self._get_value


class TestOrgEventsStream:
    async def test_stream_with_redis_messages(
        self, client: AsyncClient, test_app: FastAPI
    ) -> None:
        test_app.state._redis = _FakeRedis(
            [
                {"type": "subscribe"},
                {"type": "message", "data": b'{"type":"ping"}'},
            ]
        )
        async with client.stream("GET", f"/v1/org/{ORG_ID}/events/stream") as r:
            assert r.status_code == 200
            chunks = []
            async for chunk in r.aiter_text():
                chunks.append(chunk)
                if len(chunks) >= 2:
                    break
        body = "".join(chunks)
        assert "connected" in body


class TestMissionStream:
    async def test_mission_stream_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/stream")
        assert r.status_code == 404

    async def test_mission_stream_cross_org(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(org_id=OTHER_ORG_ID))
        r = await client.get(f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/stream")
        assert r.status_code == 404

    async def test_mission_stream_with_redis(
        self, client: AsyncClient, test_app: FastAPI, mock_service: MagicMock
    ) -> None:
        test_app.state._redis = _FakeRedis(
            [{"type": "message", "data": b'{"type":"progress"}'}]
        )
        async with client.stream(
            "GET", f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/stream"
        ) as r:
            assert r.status_code == 200
            chunks = []
            async for chunk in r.aiter_text():
                chunks.append(chunk)
                if chunks:
                    break
        assert "connected" in "".join(chunks)


class TestGraphifyStart:
    async def test_graphify_start_org_not_found(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.post(f"/v1/org/{ORG_ID}/graphify")
        assert r.status_code == 404

    async def test_graphify_start_ok_no_redis(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        with patch("app.org.router._run_graphify_job", new=AsyncMock()):
            r = await client.post(f"/v1/org/{ORG_ID}/graphify")
        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "accepted"
        assert data["org_id"] == ORG_ID

    async def test_graphify_start_ok_with_redis(
        self, client: AsyncClient, mock_service: MagicMock, test_app: FastAPI
    ) -> None:
        redis = MagicMock()
        redis.set = AsyncMock()
        test_app.state._redis = redis
        with patch("app.org.router._run_graphify_job", new=AsyncMock()):
            r = await client.post(f"/v1/org/{ORG_ID}/graphify")
        assert r.status_code == 202
        redis.set.assert_awaited()


class TestGraphifyStream:
    async def test_stream_missing_owner_record_is_not_found(
        self, client: AsyncClient, test_app: FastAPI
    ) -> None:
        test_app.state._redis = _FakeRedis([], get_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/graphify/job-1/stream")
        assert r.status_code == 404

    async def test_stream_mismatched_owner_is_not_found(
        self, client: AsyncClient, test_app: FastAPI
    ) -> None:
        import json as _json

        test_app.state._redis = _FakeRedis(
            [], get_value=_json.dumps({"tenant_id": "someone-else", "org_id": ORG_ID})
        )
        r = await client.get(f"/v1/org/{ORG_ID}/graphify/job-1/stream")
        assert r.status_code == 404

    async def test_stream_no_redis_is_not_found(self, client: AsyncClient) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/graphify/job-1/stream")
        assert r.status_code == 404

    async def test_stream_ok_with_matching_owner(
        self, client: AsyncClient, test_app: FastAPI
    ) -> None:
        import json as _json

        owner = _json.dumps({"tenant_id": TENANT_ID, "org_id": ORG_ID})
        test_app.state._redis = _FakeRedis(
            [{"type": "message", "data": _json.dumps({"type": "complete"})}],
            get_value=owner,
        )
        async with client.stream(
            "GET", f"/v1/org/{ORG_ID}/graphify/job-1/stream"
        ) as r:
            assert r.status_code == 200
            chunks = []
            async for chunk in r.aiter_text():
                chunks.append(chunk)
                if len(chunks) >= 2:
                    break
        assert "connected" in "".join(chunks)


class TestRunGraphifyJobDirect:
    async def test_run_graphify_job_success(self) -> None:
        from app.org.router import _run_graphify_job

        request = MagicMock()
        request.app.state._redis = None

        fake_final = {"nodes": 1, "edges": 2, "communities": 1}
        with (
            patch("app.org.router.asyncio.sleep", new=AsyncMock()),
            patch("app.db.session.get_session_factory") as get_factory,
            patch("app.db.rls.sqlalchemy_rls_context") as rls_ctx,
            patch(
                "app.knowledge_graph.org_builder.build_org_knowledge_graph",
                new=AsyncMock(return_value=fake_final),
            ),
        ):
            session = AsyncMock()

            @contextlib_asynccontextmanager
            async def _session_factory():
                yield session

            get_factory.return_value = lambda: _session_factory()
            rls_ctx.return_value.__aenter__ = AsyncMock(return_value=None)
            rls_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            await _run_graphify_job(ORG_ID, TENANT_ID, "job-x", request)

    async def test_run_graphify_job_failure_emits_error(self) -> None:
        from app.org.router import _run_graphify_job

        request = MagicMock()
        redis = MagicMock()
        redis.publish = AsyncMock()
        request.app.state._redis = redis

        with (
            patch("app.org.router.asyncio.sleep", new=AsyncMock()),
            patch("app.db.session.get_session_factory", side_effect=RuntimeError("no db")),
        ):
            await _run_graphify_job(ORG_ID, TENANT_ID, "job-y", request)
        redis.publish.assert_awaited()
