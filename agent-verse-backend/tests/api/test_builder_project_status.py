"""a10-F229-02: the builder project id leads to the build.

POST /builder/projects returned a random project_id that no store mapped (only
the goal's execution_context had it) and GET /builder/projects/{id} was a 501.
The id is now resolved from the persisted build goal under the caller's tenant
(real-Postgres lookup: tests/services/test_find_goals_by_context_pg.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.builder import router
from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.services.goal_service import GoalRecord, GoalService, GoalStatus
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-builder", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _client(svc: Any, ctx: TenantContext = _CTX) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _t(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    app.state.goal_service = svc
    return TestClient(app, raise_server_exceptions=False)


def _service() -> GoalService:
    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())

    async def _submit(*, goal: str, tenant_ctx: Any, execution_context: Any = None,
                      **_kw: Any) -> dict[str, Any]:
        svc._goals["build-1"] = GoalRecord(
            goal_id="build-1", goal_text=goal, status=GoalStatus.EXECUTING,
            tenant_id=tenant_ctx.tenant_id, priority="high", dry_run=False,
            created_at=datetime.now(UTC).isoformat(),
            execution_context=execution_context or {},
        )
        return {"goal_id": "build-1"}

    svc.submit_goal = _submit  # type: ignore[method-assign]
    return svc


def test_created_project_resolves_to_its_build_goal() -> None:
    svc = _service()
    client = _client(svc)
    created = client.post("/builder/projects", json={"description": "law firm site"}).json()
    status = client.get(f"/builder/projects/{created['project_id']}")
    assert status.status_code == 200
    body = status.json()
    assert body["goal_id"] == "build-1" and body["status"] == "executing"
    assert body["preview_url"] is None

    svc._goals["build-1"].status = GoalStatus.COMPLETE
    assert client.get(f"/builder/projects/{created['project_id']}").json()["status"] == "complete"


def test_unknown_or_foreign_project_is_404() -> None:
    svc = _service()
    created = _client(svc).post("/builder/projects", json={"description": "x"}).json()
    other = TenantContext(tenant_id="t-other", plan=PlanTier.FREE, api_key_id="k2")
    assert _client(svc, other).get(f"/builder/projects/{created['project_id']}").status_code == 404
    assert _client(svc).get("/builder/projects/nope").status_code == 404


def test_lookup_failure_is_503() -> None:
    svc = SimpleNamespace(find_goals_by_context=AsyncMock(side_effect=ConnectionError("db")))
    assert _client(svc).get("/builder/projects/p1").status_code == 503
