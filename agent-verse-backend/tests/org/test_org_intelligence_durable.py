"""a08-F181-01: org capability graph and decision history come from Postgres.

Both routes read process-global singletons (CapabilityGraph, DecisionIntelligence)
that nothing in app/ ever wrote and that were not tenant-keyed: they always
answered empty on every replica. They now read the tenant/org-scoped
``org_capabilities`` + department ``capability_domains`` and ``org_decisions``
rows through the RLS-scoped OrgService.
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

TENANT_ID = "00000000-0000-0000-0000-000000000001"
ORG_ID = str(uuid.uuid4())


def _cap(name: str, domain: str) -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    c.name = name
    c.domain = domain
    c.skills = ["x"]
    c.risk_level = "low"
    c.extra_data = {}
    return c


def _dept(name: str, domains: list[str]) -> MagicMock:
    d = MagicMock()
    d.id = uuid.uuid4()
    d.name = name
    d.capability_domains = domains
    return d


def _decision(dtype: str, status: str) -> MagicMock:
    d = MagicMock()
    d.id = uuid.uuid4()
    d.decision_type = dtype
    d.description = f"{dtype} decided"
    d.why = "because"
    d.entity_type = "mission"
    d.entity_id = "m-1"
    d.risk_level = "medium"
    d.autonomy_level = 2
    d.approval_status = status
    d.actor_agent_id = None
    d.created_at = datetime(2026, 10, 1, tzinfo=UTC)
    return d


@pytest.fixture
def svc() -> MagicMock:
    s = MagicMock(spec=OrgService)
    s.list_capabilities = AsyncMock(return_value=[_cap("Python backend", "engineering")])
    s.list_departments = AsyncMock(return_value=[_dept("Marketing", ["seo", "content"])])
    s.list_decisions = AsyncMock(
        return_value=[_decision("mission_create", "auto_approved"), _decision("deploy", "rejected")]
    )
    return s


@pytest.fixture
async def client(svc: MagicMock) -> Any:
    from app.org.router import get_org_service

    app = FastAPI()

    @app.middleware("http")
    async def fake_tenant(request, call_next):  # type: ignore[no-untyped-def]
        from app.tenancy.context import PlanTier, TenantContext

        request.state.tenant = TenantContext(
            tenant_id=TENANT_ID, plan=PlanTier.PROFESSIONAL, api_key_id="k", roles=("admin",)
        )
        return await call_next(request)

    app.include_router(org_router)

    async def _override():  # type: ignore[no-untyped-def]
        yield svc

    app.dependency_overrides[get_org_service] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_capabilities_come_from_the_org_rows(client: AsyncClient, svc: MagicMock) -> None:
    with patch(
        "app.org.intelligence.get_capability_graph", side_effect=AssertionError("singleton")
    ):
        r = await client.get(
            f"/v1/org/{ORG_ID}/intelligence/capabilities?required=python,seo,legal"
        )
    assert r.status_code == 200, r.text
    body = r.json()
    svc.list_capabilities.assert_awaited_once_with(ORG_ID)
    names = {c["name"] for c in body["capabilities"]}
    assert names == {"Python backend", "seo", "content"}
    assert body["total"] == 3
    gaps = body["gap_analysis"]
    assert [g["capability"] for g in gaps["gaps"]] == ["legal"]
    assert gaps["coverage_pct"] == pytest.approx(66.7)


async def test_decisions_come_from_org_decisions(client: AsyncClient, svc: MagicMock) -> None:
    with patch(
        "app.org.decision_intelligence.get_decision_intelligence",
        side_effect=AssertionError("singleton"),
    ):
        r = await client.get(f"/v1/org/{ORG_ID}/intelligence/decisions?limit=10")
    assert r.status_code == 200, r.text
    body = r.json()
    svc.list_decisions.assert_awaited_once_with(ORG_ID, limit=10)
    assert [d["decision_type"] for d in body["decisions"]] == ["mission_create", "deploy"]
    assert body["decisions"][1]["outcome"] == "rejected"
    report = body["quality_report"]
    assert report["total"] == 2
    assert report["by_status"] == {"auto_approved": 1, "rejected": 1}
