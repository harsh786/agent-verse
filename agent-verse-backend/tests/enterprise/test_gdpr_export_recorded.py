"""A ``ready`` GDPR export exists only once it is recorded (salvage RV-08).

``request_data_export`` saved the finished request best-effort: when the
``compliance_requests`` write failed it only logged a warning, kept the result in
this replica's memory and answered ``ready`` with a download link. Any other
replica (``get_export_status`` reads Postgres) then 404'd that link. A ready
export that could not be recorded is now an error (the API answers 503), and it
is not served from this replica's memory either.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.enterprise.compliance import (
    ComplianceController,
    DataExportRequest,
    ExportNotRecordedError,
)
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="gdpr-rec-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


class _BrokenWrites:
    """A DB factory whose sessions read fine (no rows) but cannot write."""

    def __init__(self) -> None:
        self.writes = 0

    def __call__(self) -> _BrokenWrites:
        return self

    async def __aenter__(self) -> _BrokenWrites:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> _BrokenWrites:
        return self

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        if "INSERT INTO compliance_requests" in sql:
            self.writes += 1
            raise ConnectionError("primary unavailable")

        class _R:
            def fetchall(self) -> list[Any]:
                return []

            def fetchone(self) -> None:
                return None

        return _R()


async def test_ready_export_that_cannot_be_recorded_raises() -> None:
    cc = ComplianceController()
    db = _BrokenWrites()
    cc._db = db
    with pytest.raises(ExportNotRecordedError):
        await cc.request_data_export(tenant_ctx=T)
    assert db.writes == 1
    # Not served from this replica's memory as if it were a recorded export.
    assert cc._export_requests == {}


async def test_failed_export_is_still_reported_when_recording_fails() -> None:
    """A failed export has no download link to break; the caller still learns why."""
    cc = ComplianceController()
    cc._db = _BrokenWrites()

    async def _boom(tenant_ctx: TenantContext, limit: int) -> list[dict[str, Any]]:
        raise RuntimeError("goals unreadable")

    cc._export_goals = _boom  # type: ignore[method-assign]
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.status == "failed"
    assert "goals" in req.payload["failed_sections"]


async def test_without_a_database_the_in_memory_export_still_works() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.status == "ready"
    assert await cc.get_export_status(request_id=req.request_id, tenant_ctx=T) is req


async def test_save_request_strict_raises_and_default_stays_best_effort() -> None:
    cc = ComplianceController()
    cc._db = _BrokenWrites()
    req = DataExportRequest(tenant_id=T.tenant_id, status="ready")
    await cc._db_save_request(req)  # best-effort default: logs, no raise
    with pytest.raises(ExportNotRecordedError):
        await cc._db_save_request(req, strict=True)


async def test_api_answers_503_when_the_export_cannot_be_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.main import create_app

    async def _not_recorded(self: Any, *, tenant_ctx: TenantContext) -> Any:
        raise ExportNotRecordedError("compliance_requests write failed")

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/tenants/signup", json={"name": "Rec", "email": "rec@test.com"})
        assert resp.status_code == 201
        c.headers["X-API-Key"] = resp.json()["api_key"]
        monkeypatch.setattr(ComplianceController, "request_data_export", _not_recorded)
        resp = await c.get("/enterprise/compliance/export")
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "5"
    assert "download_url" not in resp.text
