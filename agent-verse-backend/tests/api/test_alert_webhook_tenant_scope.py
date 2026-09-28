"""POST /webhooks/alerts/{type} only accepts alerts for the caller's own schedule.

Regression: it never checked who owned ``schedule_id`` and cached the payload
under a tenant-less key, so any tenant could inject alert context into another
tenant's alert-triggered goal.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.schedules import webhooks_router


class _Store:
    def get(self, schedule_id: str, *, tenant_ctx: Any) -> dict | None:
        return {"schedule_id": schedule_id} if (tenant_ctx.tenant_id, schedule_id) == ("t1", "s1") else None


def _client(caller: str) -> tuple[TestClient, AsyncMock]:
    app = FastAPI()

    @app.middleware("http")
    async def _auth(request: Request, call_next: Any) -> Any:
        request.state.tenant = SimpleNamespace(tenant_id=caller)
        return await call_next(request)

    app.include_router(webhooks_router)
    redis = AsyncMock()
    app.state.pools = SimpleNamespace(redis=redis)
    app.state.schedule_store = _Store()
    return TestClient(app, raise_server_exceptions=False), redis


def _url(sid: str) -> str:
    prefix = webhooks_router.prefix or ""
    return f"{prefix}/alerts/alertmanager?schedule_id={sid}"


def test_foreign_schedule_is_404_and_nothing_is_cached() -> None:
    client, redis = _client("attacker")
    assert client.post(_url("s1"), json={"alerts": []}).status_code == 404
    redis.set.assert_not_called()


def test_owner_payload_is_cached_under_a_tenant_scoped_key() -> None:
    client, redis = _client("t1")
    assert client.post(_url("s1"), json={"alerts": []}).status_code == 200
    key = redis.set.await_args.args[0]
    assert key == "alert_payload:t1:alertmanager:s1"
