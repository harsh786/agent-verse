"""Tests for the org-brain HTTP API: autonomy settings, brain decisions, and
brain-proposed mission approve/reject.

Mirrors ``tests/org/test_org_router.py`` for app construction, tenant auth,
and org/mission fixtures — same in-memory mocking pattern (no real DB).
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import sys

from app.org.brain_settings import resolve_autonomy_settings
from app.org.router import router as org_router
from app.org.service import OrgService

# NOTE: app/org/__init__.py does `from app.org.router import router`, which
# shadows the `router` attribute on the `app.org` package with the APIRouter
# instance. That means `import app.org.router as x` (attribute-based binding
# per the import statement's "as" semantics) resolves `x` to the APIRouter
# object, not the submodule -- so we pull the real module out of sys.modules
# instead, to monkeypatch symbols defined inside it (e.g. BrainDecisionStore).
org_router_module = sys.modules["app.org.router"]

# ── Helpers ───────────────────────────────────────────────────────────────────

TENANT_ID = "00000000-0000-0000-0000-000000000001"
ORG_ID = str(uuid.uuid4())
MISSION_ID = str(uuid.uuid4())
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
    m.status = "active"
    m.autonomy_level = kw.get("autonomy_level", 1)
    m.risk_tolerance = "medium"
    m.monthly_budget_usd = kw.get("monthly_budget_usd", 3000.0)
    m.goals = []
    m.policies = {}
    m.settings = kw.get("settings", {})
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
    m.title = kw.get("title", "Investigate idle capacity")
    m.description = ""
    m.objective = kw.get("objective", "Investigate idle capacity")
    m.why = "Brain proposed this proactively"
    m.expected_outcome = "A report"
    m.priority = "medium"
    m.status = kw.get("status", "proposed")
    m.mission_type = "research"
    m.source = "brain"
    m.autonomy_level = 3
    m.budget_usd = 5.0
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
            roles=("admin",),  # owner key -> org_admin (org RBAC is fail-closed)
        )
        return await call_next(request)

    app.include_router(org_router)
    return app


@pytest.fixture
def mock_service() -> MagicMock:
    svc = MagicMock(spec=OrgService)
    # Private attrs the brain-decisions endpoint reads off the service to build
    # a BrainDecisionStore against the same session/tenant (see app/org/router.py).
    svc._session = MagicMock()
    svc._tenant_id = TENANT_ID
    svc.get_organization = AsyncMock(return_value=_fake_org())
    svc.update_organization = AsyncMock(return_value=_fake_org())
    svc.get_mission = AsyncMock(return_value=_fake_mission())
    svc.update_mission_status = AsyncMock(return_value=_fake_mission(status="cancelled"))
    svc.dispatch_mission_goal = AsyncMock(return_value={"goal_id": "g1", "dispatched": True})
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
# Autonomy settings
# ══════════════════════════════════════════════════════════════════════════════


class TestAutonomyGet:
    async def test_get_autonomy_returns_level_and_resolved_settings(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        r = await client.get(f"/v1/org/{ORG_ID}/autonomy")
        assert r.status_code == 200
        data = r.json()
        assert data["autonomy_level"] == 1
        expected = asdict(resolve_autonomy_settings({}, 3000.0))
        assert data["settings"] == expected
        # derived from monthly_budget_usd since stored as 0
        assert data["settings"]["daily_budget_usd"] == pytest.approx(3000.0 / 30.0)
        assert data["settings"]["per_mission_cost_ceiling_usd"] == pytest.approx(300.0)

    async def test_get_autonomy_nonexistent_org_returns_404(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.get(f"/v1/org/{ORG_ID}/autonomy")
        assert r.status_code == 404


class TestAutonomyPatch:
    async def test_patch_sets_level_and_caps_and_get_reflects_them(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        updated_org = _fake_org(
            autonomy_level=4,
            settings={
                "autonomy": {
                    "paused": False,
                    "max_concurrent": 5,
                    "daily_budget_usd": 42.0,
                }
            },
            monthly_budget_usd=3000.0,
        )
        mock_service.update_organization = AsyncMock(return_value=updated_org)

        r = await client.patch(
            f"/v1/org/{ORG_ID}/autonomy",
            json={"autonomy_level": 4, "settings": {"max_concurrent": 5, "daily_budget_usd": 42.0}},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["autonomy_level"] == 4
        assert data["settings"]["max_concurrent"] == 5
        assert data["settings"]["daily_budget_usd"] == 42.0

        # Verify the service was asked to persist a shallow-merged autonomy block
        # plus the plain autonomy_level column, not a wholesale settings replace.
        call_args = mock_service.update_organization.call_args
        assert call_args.args[0] == ORG_ID
        persisted = call_args.args[1]
        assert persisted["autonomy_level"] == 4
        assert persisted["settings"]["autonomy"]["max_concurrent"] == 5
        assert persisted["settings"]["autonomy"]["daily_budget_usd"] == 42.0

        # GET reflects the same resolved view
        mock_service.get_organization = AsyncMock(return_value=updated_org)
        r2 = await client.get(f"/v1/org/{ORG_ID}/autonomy")
        assert r2.status_code == 200
        assert r2.json() == data

    async def test_patch_merge_preserves_existing_autonomy_keys(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        org = _fake_org(settings={"autonomy": {"paused": True, "max_concurrent": 2}})
        mock_service.get_organization = AsyncMock(return_value=org)
        mock_service.update_organization = AsyncMock(return_value=org)

        await client.patch(f"/v1/org/{ORG_ID}/autonomy", json={"settings": {"max_concurrent": 9}})

        persisted = mock_service.update_organization.call_args.args[1]
        # existing 'paused' key must survive the shallow merge
        assert persisted["settings"]["autonomy"]["paused"] is True
        assert persisted["settings"]["autonomy"]["max_concurrent"] == 9

    async def test_patch_out_of_range_autonomy_level_rejected(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        r = await client.patch(f"/v1/org/{ORG_ID}/autonomy", json={"autonomy_level": 6})
        assert r.status_code in (400, 422)
        mock_service.update_organization.assert_not_called()

        r2 = await client.patch(f"/v1/org/{ORG_ID}/autonomy", json={"autonomy_level": -1})
        assert r2.status_code in (400, 422)

    async def test_patch_nonexistent_org_returns_404(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_organization = AsyncMock(return_value=None)
        r = await client.patch(f"/v1/org/{ORG_ID}/autonomy", json={"autonomy_level": 2})
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# Brain decisions
# ══════════════════════════════════════════════════════════════════════════════


class TestBrainDecisions:
    async def test_list_decisions_returns_recorded_rows(
        self, client: AsyncClient, mock_service: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            {
                "id": str(uuid.uuid4()),
                "tick_id": "t1",
                "kind": "propose",
                "rationale": "org has been idle",
                "target_goal": "reduce idle capacity",
                "action": "proposed",
                "guardrail_verdict": "allow",
                "reason": "",
                "est_cost_usd": 0.5,
                "mission_id": MISSION_ID,
                "created_at": NOW.isoformat(),
            }
        ]

        class _FakeStore:
            def __init__(self, session: Any) -> None:
                self.session = session

            async def list(self, org_id: str, tenant_id: str, limit: int = 50) -> list[dict]:
                assert org_id == ORG_ID
                assert tenant_id == TENANT_ID
                return rows[:limit]

        monkeypatch.setattr(org_router_module, "BrainDecisionStore", _FakeStore)

        r = await client.get(f"/v1/org/{ORG_ID}/brain/decisions")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["kind"] == "propose"
        assert data[0]["mission_id"] == MISSION_ID

    async def test_list_decisions_respects_limit_query_param(
        self, client: AsyncClient, mock_service: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen_limit: dict[str, int] = {}

        class _FakeStore:
            def __init__(self, session: Any) -> None:
                pass

            async def list(self, org_id: str, tenant_id: str, limit: int = 50) -> list[dict]:
                seen_limit["limit"] = limit
                return []

        monkeypatch.setattr(org_router_module, "BrainDecisionStore", _FakeStore)

        r = await client.get(f"/v1/org/{ORG_ID}/brain/decisions?limit=5")
        assert r.status_code == 200
        assert seen_limit["limit"] == 5


# ══════════════════════════════════════════════════════════════════════════════
# Approve / reject brain proposals
# ══════════════════════════════════════════════════════════════════════════════


class TestBrainProposalApprove:
    async def test_approve_proposed_mission_dispatches_and_returns_result(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(status="proposed"))
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/approve")
        assert r.status_code == 200
        data = r.json()
        assert data["dispatched"] is True
        assert data["goal_id"] == "g1"
        mock_service.dispatch_mission_goal.assert_awaited_once()
        call = mock_service.dispatch_mission_goal.call_args
        assert call.args[0] == MISSION_ID

    async def test_approve_nonexistent_mission_returns_404(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=None)
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/approve")
        assert r.status_code == 404

    async def test_approve_non_proposed_mission_returns_409(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(status="active"))
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/approve")
        assert r.status_code == 409
        mock_service.dispatch_mission_goal.assert_not_awaited()


class TestBrainProposalReject:
    async def test_reject_proposed_mission_cancels_it(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(status="proposed"))
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/reject")
        assert r.status_code == 200
        mock_service.update_mission_status.assert_awaited_once_with(MISSION_ID, "cancelled")
        data = r.json()
        assert data["status"] == "cancelled"

    async def test_reject_nonexistent_mission_returns_404(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=None)
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/reject")
        assert r.status_code == 404

    async def test_reject_non_proposed_mission_returns_409(
        self, client: AsyncClient, mock_service: MagicMock
    ) -> None:
        mock_service.get_mission = AsyncMock(return_value=_fake_mission(status="completed"))
        r = await client.post(f"/v1/org/{ORG_ID}/brain/proposals/{MISSION_ID}/reject")
        assert r.status_code == 409
        mock_service.update_mission_status.assert_not_awaited()


class TestBrainRouterAuth:
    async def test_unauthenticated_get_autonomy_returns_401(self) -> None:
        app = FastAPI()
        app.include_router(org_router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"/v1/org/{ORG_ID}/autonomy")
        assert r.status_code == 401
