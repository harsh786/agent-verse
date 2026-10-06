"""a08-F197-04: a usage rollup that cannot be read is an error, not zero usage.

``get_usage_summary`` caught the DB rollup failure, logged
``usage_summary_db_failed`` and returned only the in-memory buffer (empty on
every replica that metered nothing itself), so ``GET /billing/usage`` showed a
paying tenant zero usage during an outage. It now raises
``UsageSummaryUnavailableError`` and the endpoint answers 503 (retryable).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.usage_service import UsageService, UsageSummaryUnavailableError


def _down_factory() -> Any:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=ConnectionError("postgres is down"))

    @asynccontextmanager
    async def _begin() -> Any:
        yield None

    session.begin = MagicMock(side_effect=lambda: _begin())

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


async def test_rollup_failure_raises_instead_of_returning_the_buffer() -> None:
    svc = UsageService(db_factory=_down_factory())
    await svc.record(tenant_id="t1", metric="goals", quantity=1)
    with pytest.raises(UsageSummaryUnavailableError):
        await svc.get_usage_summary("t1")


async def test_no_db_build_still_summarises_the_buffer() -> None:
    svc = UsageService()
    await svc.record(tenant_id="t1", metric="goals", quantity=2)
    summary = await svc.get_usage_summary("t1")
    assert summary["usage"] == {"goals": 2.0}


def test_billing_usage_is_503_during_an_outage() -> None:
    from app.api.billing import router as billing_router
    from app.tenancy.context import PlanTier, TenantContext

    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext("t1", PlanTier.STARTER, "k", roles=("admin",))
        return await call_next(request)

    app.include_router(billing_router)
    app.state.usage_service = UsageService(db_factory=_down_factory())
    resp = TestClient(app, raise_server_exceptions=False).get("/billing/usage")
    assert resp.status_code == 503, resp.text
