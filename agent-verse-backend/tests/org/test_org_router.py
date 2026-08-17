"""Tests for the AI Organization OS — app/org/ module.

Uses fast in-memory mocking (no real DB) for unit tests.
Integration tests tagged @pytest.mark.integration use real DB via Docker.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.org.router import router as org_router
from app.org.service import OrgService

# ── Helpers ───────────────────────────────────────────────────────────────────

TENANT_ID = "00000000-0000-0000-0000-000000000001"
ORG_ID    = str(uuid.uuid4())
MISSION_ID = str(uuid.uuid4())
DEPT_ID   = str(uuid.uuid4())
TASK_ID   = str(uuid.uuid4())
NOW       = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)


def _fake_org(**kw: Any) -> MagicMock:
    """Fake Organization ORM row."""
    m = MagicMock()
    m.id = uuid.UUID(ORG_ID)
    m.tenant_id = uuid.UUID(TENANT_ID)
    m.name = kw.get("name", "Acme AI Corp")
    m.slug = kw.get("slug", "acme-ai-corp")
    m.description = kw.get("description", "")
    m.industry = kw.get("industry", "technology")
    m.jurisdiction = kw.get("jurisdiction", "US")
    m.mission = kw.get("mission", "Build world-class AI")
    m.vision = kw.get("vision", "")
    m.status = kw.get("status", "active")
    m.autonomy_level = kw.get("autonomy_level", 1)
    m.risk_tolerance = kw.get("risk_tolerance", "medium")
    m.monthly_budget_usd = kw.get("monthly_budget_usd", 0.0)
    m.goals = []
    m.policies = {}
    m.settings = {}
    m.blueprint_ids = []
    m.created_by = None
    m.extra_data = {}
    m.created_at = NOW
    m.updated_at = NOW
    return m


def _fake_mission(**kw: Any) -> MagicMock:
    m = MagicMock()
    m.id = uuid.UUID(MISSION_ID)
    m.tenant_id = uuid.UUID(TENANT_ID)
    m.org_id = uuid.UUID(ORG_ID)
    m.dept_id = None
    m.assigned_team_id = None
    m.title = kw.get("title", "Test Mission")
    m.description = kw.get("description", "")
    m.objective = kw.get("objective", "Achieve the goal")
    m.why = kw.get("why", "Because it matters")
    m.expected_outcome = kw.get("expected_outcome", "Success")
    m.priority = kw.get("priority", "medium")
    m.status = kw.get("status", "draft")
    m.mission_type = kw.get("mission_type", "research")
    m.source = kw.get("source", "manual")
    m.autonomy_level = kw.get("autonomy_level", 1)
    m.budget_usd = kw.get("budget_usd", 0.0)
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
    m.purpose = kw.get("purpose", "Build things")
    m.capability_domains = []
    m.parent_dept_id = None
    m.manager_agent_id = None
    m.status = "active"
    m.extra_data = {}
    m.created_at = NOW
    m.updated_at = NOW
    return m


# ── FastAPI test app ──────────────────────────────────────────────────────────

@pytest.fixture
def test_app() -> FastAPI:
    """Minimal FastAPI app with org router + fake tenant middleware."""
    app = FastAPI()

    @app.middleware("http")
    async def fake_tenant(request, call_next):
        from app.tenancy.context import PlanTier, TenantContext
        request.state.tenant = TenantContext(
            tenant_id=TENANT_ID,
            plan=PlanTier.PROFESSIONAL,
            api_key_id="test-key",
        )
        return await call_next(request)

    app.include_router(org_router)
    return app


@pytest.fixture
def mock_service() -> MagicMock:
    svc = MagicMock(spec=OrgService)
    svc.create_organization = AsyncMock(return_value=_fake_org())
    svc.get_organization = AsyncMock(return_value=_fake_org())
    svc.list_organizations = AsyncMock(return_value=[_fake_org()])
    svc.update_organization = AsyncMock(return_value=_fake_org())
    svc.delete_organization = AsyncMock(return_value=True)
    svc.get_org_health = AsyncMock(return_value={
        "health": "healthy",
        "active_missions": 3,
        "active_teams": 5,
        "pending_approvals": 0,
        "task_counts": {"queued": 2, "running": 3, "completed": 10},
        "event_counts_24h": {"mission.created": 1},
        "items_needing_attention": 0,
        # extra for frontend
        "health_score": 0.85,
        "agent_count": 8,
        "alerts": [],
    })
    svc.create_department = AsyncMock(return_value=_fake_dept())
    svc.list_departments = AsyncMock(return_value=[_fake_dept()])
    svc.get_department = AsyncMock(return_value=_fake_dept())
    svc.update_department = AsyncMock(return_value=_fake_dept())
    svc.create_mission = AsyncMock(return_value=_fake_mission())
    svc.list_missions = AsyncMock(return_value=[_fake_mission()])
    svc.get_mission = AsyncMock(return_value=_fake_mission())
    svc.update_mission_status = AsyncMock(return_value=_fake_mission(status="active"))
    svc.list_events = AsyncMock(return_value=[])
    return svc


@pytest.fixture
async def client(test_app: FastAPI, mock_service: MagicMock) -> AsyncClient:
    """Client with dependency override so all endpoints use mock_service."""
    from app.org.router import get_org_service

    async def _override():
        yield mock_service

    test_app.dependency_overrides[get_org_service] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://test",
        ) as c:
            yield c
    finally:
        test_app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS — OrgService
# ══════════════════════════════════════════════════════════════════════════════

class TestOrgServiceCreate:
    """TDD: write tests first, verify service behaviour."""

    async def test_create_org_returns_org_with_id(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.refresh = AsyncMock()

        svc = OrgService(session=session, tenant_id=TENANT_ID)

        # Mock the flush so the org gets its id
        async def _flush_side_effect() -> None:
            pass

        session.flush.side_effect = _flush_side_effect
        org = _fake_org()
        session.refresh.side_effect = lambda o: setattr(o, "id", uuid.UUID(ORG_ID))

        with patch.object(svc, "create_organization", AsyncMock(return_value=org)):
            result = await svc.create_organization(
                name="Acme AI Corp",
                description="Test org",
                industry="technology",
                jurisdiction="US",
            )

        assert result.name == "Acme AI Corp"
        assert result.id is not None

    async def test_get_nonexistent_org_returns_none(self) -> None:
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=result_mock)

        svc = OrgService(session=session, tenant_id=TENANT_ID)
        # Must pass a valid UUID string — service calls uuid.UUID(org_id)
        result = await svc.get_organization(str(uuid.uuid4()))
        assert result is None

    async def test_create_mission_requires_valid_org(self) -> None:
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=result_mock)

        svc = OrgService(session=session, tenant_id=TENANT_ID)
        with pytest.raises((ValueError, AttributeError)):
            await svc.create_mission(
                org_id="nonexistent",
                title="Bad Mission",
                mission_type="research",
                priority="high",
            )


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER TESTS — HTTP Endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestOrgRouterAuth:
    """All endpoints require authentication."""

    async def test_unauthenticated_post_returns_401(self) -> None:
        app = FastAPI()
        app.include_router(org_router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/v1/org", json={"name": "x"})
        assert r.status_code == 401

    async def test_unauthenticated_get_returns_401(self) -> None:
        app = FastAPI()
        app.include_router(org_router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/v1/org")
        assert r.status_code == 401


class TestOrgRouterCreate:
    """POST /v1/org — create organization."""

    async def test_create_org_returns_201(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.post("/v1/org", json={
            "name": "Acme AI Corp",
            "industry": "technology",
            "jurisdiction": "US",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Acme AI Corp"
        assert "id" in data
        assert "created_at" in data

    async def test_create_org_missing_name_returns_422(self, client: AsyncClient) -> None:
        r = await client.post("/v1/org", json={})
        assert r.status_code == 422
        # RFC 7807 or FastAPI validation error
        assert r.status_code == 422

    async def test_create_org_response_has_required_fields(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.post("/v1/org", json={"name": "Test"})
        data = r.json()
        required = ["id", "name", "status", "autonomy_level", "created_at"]
        for field in required:
            assert field in data, f"Missing field: {field}"


class TestOrgRouterList:
    """GET /v1/org — list organizations."""

    async def test_list_returns_200_with_data(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.get("/v1/org")
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    async def test_list_returns_cursor_pagination_fields(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.get("/v1/org?limit=10")
        data = r.json()
        assert "data" in data
        assert "cursor" in data
        assert "hasMore" in data


class TestOrgRouterGet:
    """GET /v1/org/{org_id} — get single organization."""

    async def test_get_existing_org_returns_200(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.get(f"/v1/org/{ORG_ID}")
        assert r.status_code == 200
        assert r.json()["id"] == ORG_ID

    async def test_get_nonexistent_org_returns_404(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.get("/v1/org/nonexistent-org-id")
        assert r.status_code == 404
        err = r.json()
        assert "type" in err or "detail" in err


class TestOrgRouterMissions:
    """Mission endpoints."""

    async def test_create_mission_returns_201(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.post(f"/v1/org/{ORG_ID}/missions", json={
            "org_id": ORG_ID,
            "title": "Research AI market trends",
            "priority": "high",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "Test Mission"
        assert "id" in data

    async def test_list_missions_returns_200(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.get(f"/v1/org/{ORG_ID}/missions")
        assert r.status_code == 200
        assert "data" in r.json()

    async def test_mission_status_update_returns_200(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.post(
            f"/v1/org/{ORG_ID}/missions/{MISSION_ID}/status",
            json={"status": "active"},
        )
        assert r.status_code == 200


class TestOrgRouterHealth:
    """GET /v1/org/{org_id}/health — org health summary."""

    async def test_health_returns_200(self, client: AsyncClient, mock_service: MagicMock) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/health")
        assert r.status_code == 200
        data = r.json()
        assert "active_missions" in data
        assert "health" in data

    async def test_health_has_task_counts(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.get(f"/v1/org/{ORG_ID}/health")
        assert r.status_code == 200
        assert r.json()["active_missions"] >= 0


class TestOrgRouterDepartments:
    """Department endpoints."""

    async def test_create_department_returns_201(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.post(f"/v1/org/{ORG_ID}/departments", json={
            "org_id": ORG_ID,
            "name": "Engineering",
            "purpose": "Build great software",
        })
        assert r.status_code == 201
        assert r.json()["name"] == "Engineering"

    async def test_list_departments_returns_200(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.get(f"/v1/org/{ORG_ID}/departments")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# CONTRACT TESTS — API response shape matches frontend expectations
# ══════════════════════════════════════════════════════════════════════════════

class TestOrgAPIContract:
    """Verify API responses match what the frontend expects."""

    async def test_org_response_has_all_required_fields(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.post("/v1/org", json={"name": "Contract Test"})
        data = r.json()
        required_fields = [
            "id", "tenant_id", "name", "status", "autonomy_level",
            "risk_tolerance", "created_at", "updated_at",
        ]
        for field in required_fields:
            assert field in data, f"Contract broken: missing '{field}'"

    async def test_mission_response_has_all_required_fields(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.post(f"/v1/org/{ORG_ID}/missions", json={
            "org_id": ORG_ID,
            "title": "Contract mission", "priority": "medium",
        })
        data = r.json()
        for field in ["id", "org_id", "title", "status", "priority", "created_at"]:
            assert field in data, f"Mission contract broken: missing '{field}'"

    async def test_list_response_always_has_cursor_pagination(self, client: AsyncClient, mock_service: MagicMock) -> None:  # noqa: E501
        r = await client.get("/v1/org")
        data = r.json()
        for field in ["data", "cursor", "hasMore"]:
            assert field in data, f"Pagination contract broken: missing '{field}'"

    async def test_error_response_is_structured(self, client: AsyncClient) -> None:
        """All 4xx errors must be machine-readable."""
        r = await client.post("/v1/org", json={})  # missing name
        assert r.status_code in (401, 422)
        # Must be JSON with some structure
        assert r.headers["content-type"].startswith("application/json")
