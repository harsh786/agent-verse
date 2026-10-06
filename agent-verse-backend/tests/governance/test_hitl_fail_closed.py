"""Regression tests: HITL resolution fails closed and approvals get an expiry.

1. ``_db_update_resolution`` returned ``True`` on a DB exception (fail-open), so
   the approve/reject endpoints answered 200 while the ``approval_requests``
   row stayed ``pending``.
2. ``approve_async`` approved locally (unblocking the agent) BEFORE the DB
   compare-and-swap, so a failed or lost write still let the gated action run.
3. The INSERT omitted ``expires_at``; the beat expiry
   (``WHERE expires_at < NOW()``) therefore never matched any row.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.governance.hitl import (
    ApprovalStatus,
    HITLGateway,
    HITLResolutionUnavailableError,
)
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(
    tenant_id="t-hitl", plan=PlanTier.PROFESSIONAL, api_key_id="k", roles=("admin",)
)


def _failing_factory() -> Any:
    session = MagicMock()
    session.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
    session.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=session)


class _RecordingSession:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._calls = calls

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> _RecordingSession:
        return self

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self._calls.append((str(stmt), dict(params or {})))
        return MagicMock(rowcount=1)


@pytest.mark.asyncio
async def test_db_update_resolution_raises_on_db_error() -> None:
    gw = HITLGateway()
    gw._db_session_factory = _failing_factory()
    with pytest.raises(HITLResolutionUnavailableError):
        await gw._db_update_resolution("r1", "t1", "approved", "alice")


@pytest.mark.asyncio
async def test_approve_async_does_not_unblock_the_agent_when_the_write_fails() -> None:
    gw = HITLGateway()
    req = gw.request_approval(goal_id="g", action="deploy prod", tenant_ctx=CTX)
    gw._db_session_factory = MagicMock()  # a DB is configured ...
    gw.aget_request = AsyncMock(return_value=req)  # type: ignore[method-assign]
    gw._db_update_resolution = AsyncMock(  # type: ignore[method-assign]
        side_effect=HITLResolutionUnavailableError("db down")
    )
    with pytest.raises(HITLResolutionUnavailableError):
        await gw.approve_async(req.request_id, approver="alice", tenant_ctx=CTX)
    assert req.status == ApprovalStatus.PENDING
    assert not req._event.is_set()


@pytest.mark.asyncio
async def test_approve_async_lost_race_takes_the_db_outcome() -> None:
    gw = HITLGateway()
    req = gw.request_approval(goal_id="g", action="deploy prod", tenant_ctx=CTX)
    gw._db_session_factory = MagicMock()
    gw.aget_request = AsyncMock(return_value=req)  # type: ignore[method-assign]
    gw._db_update_resolution = AsyncMock(return_value=False)  # type: ignore[method-assign]
    gw._db_read_status = AsyncMock(return_value="rejected")  # type: ignore[method-assign]
    ok = await gw.approve_async(req.request_id, approver="alice", tenant_ctx=CTX)
    assert ok is False
    assert req.status == ApprovalStatus.REJECTED


@pytest.mark.asyncio
async def test_approve_async_wins_then_approves_once() -> None:
    gw = HITLGateway()
    req = gw.request_approval(goal_id="g", action="deploy prod", tenant_ctx=CTX)
    gw._db_session_factory = MagicMock()
    gw.aget_request = AsyncMock(return_value=req)  # type: ignore[method-assign]
    gw._db_update_resolution = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert await gw.approve_async(req.request_id, approver="alice", tenant_ctx=CTX) is True
    assert req.status == ApprovalStatus.APPROVED
    assert gw._db_update_resolution.await_count == 1


@pytest.mark.asyncio
async def test_reject_propagates_db_error_instead_of_reporting_success() -> None:
    gw = HITLGateway()
    req = gw.request_approval(goal_id="g", action="deploy prod", tenant_ctx=CTX)
    gw._db_session_factory = _failing_factory()
    with pytest.raises(HITLResolutionUnavailableError):
        await gw.reject(req.request_id, approver="bob", tenant_ctx=CTX)
    assert req.status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_persisted_request_carries_expires_at() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    gw = HITLGateway(timeout_seconds=120)
    gw._db_session_factory = lambda: _RecordingSession(calls)
    req = gw.request_approval(goal_id="g", action="deploy prod", tenant_ctx=CTX)
    await gw._db_persist_approval_request(req, CTX.tenant_id)
    inserts = [(sql, p) for sql, p in calls if "INSERT INTO approval_requests" in sql]
    assert inserts, calls
    sql, params = inserts[-1]
    assert "expires_at" in sql
    assert params["expires_at"] is not None
    assert params["expires_at"] == req._expires_at_dt


def test_approve_endpoint_returns_503_when_decision_cannot_be_recorded() -> None:
    from app.api import governance as gov_api

    gw = HITLGateway()
    gw.approve_async = AsyncMock(  # type: ignore[method-assign]
        side_effect=HITLResolutionUnavailableError("db down")
    )
    # The route asks for the vote outcome (a03-F056-07), which is the same call.
    gw.approve_async_outcome = AsyncMock(  # type: ignore[method-assign]
        side_effect=HITLResolutionUnavailableError("db down")
    )
    gw.reject = AsyncMock(  # type: ignore[method-assign]
        side_effect=HITLResolutionUnavailableError("db down")
    )
    app = FastAPI()
    app.state.hitl_gateway = gw

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    app.include_router(gov_api.router)
    client = TestClient(app, raise_server_exceptions=False)
    r1 = client.post("/governance/approvals/r1/approve", json={"approver": "alice", "note": ""})
    assert r1.status_code == 503, r1.text
    r2 = client.post("/governance/approvals/r1/reject", json={"approver": "alice", "note": ""})
    assert r2.status_code == 503, r2.text


def _history_client(factory: Any) -> TestClient:
    from app.api import governance as gov_api

    app = FastAPI()
    app.state.hitl_gateway = HITLGateway()
    app.state.db_session_factory = factory

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    app.include_router(gov_api.router)
    return TestClient(app, raise_server_exceptions=False)


def test_approval_history_sets_the_tenant_rls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import governance as gov_api

    monkeypatch.setattr(gov_api, "_org_gate_approvals", AsyncMock(return_value=[]))
    calls: list[tuple[str, dict[str, Any]]] = []

    class _Session(_RecordingSession):
        async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
            await super().execute(stmt, params)
            return MagicMock(fetchall=MagicMock(return_value=[]))

    resp = _history_client(lambda: _Session(calls)).get("/governance/approvals/history")
    assert resp.status_code == 200, resp.text
    guc = [p for sql, p in calls if "set_config('app.tenant_id'" in sql]
    assert guc, calls
    assert guc[0]["tid"] == CTX.tenant_id


def test_approval_history_db_error_is_503_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import governance as gov_api

    monkeypatch.setattr(gov_api, "_org_gate_approvals", AsyncMock(return_value=[]))
    resp = _history_client(_failing_factory()).get("/governance/approvals/history")
    assert resp.status_code == 503
