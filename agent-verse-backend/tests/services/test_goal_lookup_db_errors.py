"""GOAL-LOOKUP-503: a database outage is a 503, never a 404 "Goal not found".

``GoalService._db_get_goal_record`` caught every exception and returned
``None`` — the same value as "no such row" — so during a Postgres outage every
goal read (GET /goals/{id}, events, stream, cancel, pause ...) answered 404 and
clients concluded the goal had been deleted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.state import GoalStatus
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


def _async_cm(value: Any) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _db(*, row: Any = None, error: Exception | None = None) -> MagicMock:
    session = AsyncMock()
    if error is not None:
        session.execute = AsyncMock(side_effect=error)
    else:
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=row))
        )
    session.begin = MagicMock(return_value=_async_cm(None))
    db = MagicMock()
    db.return_value = _async_cm(session)
    return db


def _outage() -> MagicMock:
    return _db(error=ConnectionRefusedError("connection refused"))


@pytest.mark.asyncio
async def test_db_outage_is_service_unavailable_not_not_found() -> None:
    svc = GoalService(db_session_factory=_outage())
    with pytest.raises(ServiceUnavailableError):
        await svc._db_get_goal_record("g-missing", CTX)
    with pytest.raises(ServiceUnavailableError):
        await svc.get_goal("g-missing", CTX)
    with pytest.raises(ServiceUnavailableError):
        await svc.get_events("g-missing", CTX)
    with pytest.raises(ServiceUnavailableError):
        await svc.get_pattern_selection("g-missing", CTX)


@pytest.mark.asyncio
async def test_missing_row_is_still_not_found() -> None:
    svc = GoalService(db_session_factory=_db(row=None))
    assert await svc._db_get_goal_record("g-missing", CTX) is None
    with pytest.raises(NotFoundError):
        await svc.get_goal("g-missing", CTX)


@pytest.mark.asyncio
async def test_refresh_of_a_cached_goal_does_not_serve_stale_state_on_outage() -> None:
    """A replica holding a (possibly stale) copy must not present it as current
    when the authoritative row cannot be read."""
    svc = GoalService(db_session_factory=_outage(), task_queue=MagicMock())
    svc._goals["g1"] = GoalRecord(
        goal_id="g1",
        goal_text="x",
        status=GoalStatus.EXECUTING,
        tenant_id="t1",
        priority="normal",
        dry_run=False,
        created_at="",
    )
    with pytest.raises(ServiceUnavailableError):
        await svc.get_goal("g1", CTX)


@pytest.mark.asyncio
async def test_api_maps_outage_to_503() -> None:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from httpx import ASGITransport, AsyncClient

    from app.core.errors import PlatformError

    svc = GoalService(db_session_factory=_outage())
    app = FastAPI()

    @app.exception_handler(PlatformError)
    async def _h(_: Any, exc: PlatformError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    @app.get("/goals/{goal_id}")
    async def _get(goal_id: str) -> dict[str, Any]:
        return await svc.get_goal(goal_id, CTX)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/goals/g-missing")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "GOAL_STORE_UNAVAILABLE"
