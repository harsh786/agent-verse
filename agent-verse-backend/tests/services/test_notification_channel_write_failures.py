"""a08-F196-03: a notification channel write that did not reach Postgres is a 503.

``_persist_channel`` caught every exception and logged
``notification_persist_failed``; ``add_channel_async`` returned normally, so
``POST /governance/notifications`` answered "created" for a channel that existed
only in one pod's memory (and was gone after a restart). A failed delete
returned False, so ``DELETE`` answered 404 while the row stayed and every pod
kept notifying it. Both are now ``NotificationStoreUnavailableError`` (503,
retryable); the cache is rolled back to match the DB.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.notification_service import (
    NotificationChannel,
    NotificationService,
    NotificationStoreUnavailableError,
)


def _factory(*, fail_on: str) -> Any:
    """Session factory whose ``fail_on`` statement (INSERT / DELETE) raises."""
    load = MagicMock()
    load.fetchall.return_value = []

    async def _execute(stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        if fail_on in sql:
            raise ConnectionError("postgres is down")
        return load

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)

    @asynccontextmanager
    async def _begin() -> Any:
        yield None

    session.begin = MagicMock(side_effect=lambda: _begin())

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


def _channel(cid: str = "c1") -> NotificationChannel:
    return NotificationChannel(
        channel_id=cid,
        tenant_id="t1",
        channel_type="webhook",
        config={"url": "https://hooks.example.com/x"},
    )


async def test_failed_insert_raises_and_caches_nothing() -> None:
    svc = NotificationService()
    svc.set_db(_factory(fail_on="INSERT"))
    with pytest.raises(NotificationStoreUnavailableError) as exc:
        await svc.add_channel_async(_channel())
    assert exc.value.http_status == 503 and exc.value.retryable
    assert svc.get_channels("t1") == []
    assert "c1" not in svc._pending_writes


async def test_failed_delete_raises_and_keeps_the_channel() -> None:
    svc = NotificationService()
    svc.set_db(_factory(fail_on="DELETE"))
    svc._channels["t1"] = [_channel()]
    svc._loaded_tenants["t1"] = 10**12  # fresh: no refresh during the call
    with pytest.raises(NotificationStoreUnavailableError):
        await svc.remove_channel_async("c1", "t1")
    assert [c.channel_id for c in svc.get_channels("t1")] == ["c1"]
    assert "c1" not in svc._pending_deletes


async def test_legacy_background_write_failure_is_logged_not_raised() -> None:
    import asyncio

    svc = NotificationService()
    svc.set_db(_factory(fail_on="INSERT"))
    svc.add_channel(_channel("c2"))
    await asyncio.gather(*list(svc._bg_tasks))  # must not raise


def _app(svc: NotificationService) -> TestClient:
    from app.api.governance import router as governance_router
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.middleware import TenantMiddleware

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return TenantContext("t1", PlanTier.STARTER, key, roles=("admin",))

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(governance_router)
    app.state.notification_service = svc
    return TestClient(app, raise_server_exceptions=False)


def test_post_notifications_is_503_not_created_when_the_row_was_not_written() -> None:
    svc = NotificationService()
    svc.set_db(_factory(fail_on="INSERT"))
    client = _app(svc)
    resp = client.post(
        "/governance/notifications",
        json={"channel_type": "webhook", "config": {"url": "https://hooks.example.com/x"}},
        headers={"X-API-Key": "k"},
    )
    assert resp.status_code == 503, resp.text
    assert svc.get_channels("t1") == []


def test_delete_notification_is_503_not_404_when_the_delete_failed() -> None:
    svc = NotificationService()
    svc.set_db(_factory(fail_on="DELETE"))
    client = _app(svc)
    resp = client.delete("/governance/notifications/c-remote", headers={"X-API-Key": "k"})
    assert resp.status_code == 503, resp.text
