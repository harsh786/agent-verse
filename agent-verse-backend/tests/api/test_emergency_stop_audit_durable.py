"""INC-01: the emergency stop reports ``audit_recorded`` only once the audit row is stored.

The stop's audit entry went through the fire-and-forget ``AuditLog.record``, so
``audit_recorded: true`` meant only "queued". It is now awaited with
``record_async``; a failed write is reported (``audit_recorded: false``,
``audit_failed`` in errors, partial) — the stop itself stays in force.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.governance import router as governance_router
from app.governance.audit import AuditLog, AuditWriteError
from app.governance.hitl import HITLGateway
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(
    tenant_id="t-estop-audit", plan=PlanTier.ENTERPRISE, api_key_id="kid", roles=("admin",)
)


def _client(audit: AuditLog) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(governance_router)
    app.state.hitl_gateway = HITLGateway()
    app.state.audit_log = audit
    app.state._redis = fakeredis.aioredis.FakeRedis()
    return TestClient(app, raise_server_exceptions=False)


def test_audit_recorded_means_the_row_was_written(monkeypatch: Any) -> None:
    audit = AuditLog()
    written: list[str] = []

    async def _persist(event: Any, tenant_id: str) -> None:
        written.append(event.outcome)

    audit._db = object()
    monkeypatch.setattr(audit, "_persist_with_retry", _persist)
    body = _client(audit).post("/governance/emergency-stop", headers={"X-API-Key": "k"}).json()
    assert written == ["stop_activated"]
    assert body["audit_recorded"] is True


def test_failed_audit_write_is_reported(monkeypatch: Any) -> None:
    audit = AuditLog()

    async def _fail(event: Any, tenant_id: str) -> None:
        raise AuditWriteError("db down")

    audit._db = object()
    monkeypatch.setattr(audit, "_persist_with_retry", _fail)
    resp = _client(audit).post("/governance/emergency-stop", headers={"X-API-Key": "k"})
    assert resp.status_code == 200  # the stop is persisted and in force
    body = resp.json()
    assert body["active"] is True
    assert body["audit_recorded"] is False
    assert body["partial"] is True
    assert any("audit_failed" in e for e in body["errors"])
