"""SVC-02: Idempotency-Key fails closed, is bound to the request, and owns its claim.

* With no idempotency store wired the header was silently ignored and the
  submission ran unguarded.
* A reused key with a DIFFERENT body replayed the first response.
* The 120 s pending claim was not owner-checked and never extended, so a slow
  submission let a retry create a second goal.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api import goals as goals_api
from app.api.goals import router as goals_router
from app.reliability import idempotency as idem_mod
from app.reliability.idempotency import IdempotencyStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-bind", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_KEY = "ak_test_bind"
_H = {"X-API-Key": _KEY, "Idempotency-Key": "idem-bind"}


def _app(svc: Any, store: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(goals_router)
    app.state.goal_service = svc
    if store is not None:
        app.state.idempotency_store = store
    return app


def _store() -> IdempotencyStore:
    return IdempotencyStore(fakeredis.aioredis.FakeRedis(decode_responses=True))


def test_header_without_store_fails_closed() -> None:
    svc = AsyncMock()
    client = TestClient(_app(svc, None), raise_server_exceptions=False)
    resp = client.post("/goals", json={"goal": "do it"}, headers=_H)
    assert resp.status_code == 503
    svc.submit_goal.assert_not_awaited()


def test_no_header_without_store_still_submits() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {"goal_id": "g", "status": "planning"}
    client = TestClient(_app(svc, None), raise_server_exceptions=False)
    resp = client.post("/goals", json={"goal": "do it"}, headers={"X-API-Key": _KEY})
    assert resp.status_code == 202


def test_same_key_different_body_is_rejected() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {"goal_id": "g-first", "status": "planning"}
    client = TestClient(_app(svc, _store()), raise_server_exceptions=False)
    assert client.post("/goals", json={"goal": "do it"}, headers=_H).status_code == 202
    other = client.post("/goals", json={"goal": "something else"}, headers=_H)
    assert other.status_code == 422
    assert other.json()["detail"]["error"] == "idempotency_key_reused"
    same = client.post("/goals", json={"goal": "do it"}, headers=_H)
    assert same.status_code == 202 and same.json()["goal_id"] == "g-first"
    assert svc.submit_goal.await_count == 1


async def test_stale_owner_cannot_complete_or_release_a_newer_claim() -> None:
    store = _store()
    assert await store.claim("k", "t", owner="A", body_hash="h", pending_ttl_seconds=60) is None
    # A's pending claim lapses; B claims the key.
    await store._redis.delete(store._key("k", "t"))
    assert await store.claim("k", "t", owner="B", body_hash="h", pending_ttl_seconds=60) is None
    assert await store.complete("k", "t", {"goal_id": "gA"}, owner="A", body_hash="h") is False
    await store.release("k", "t", owner="A", body_hash="h")
    entry = await store.claim("k", "t", owner="C", body_hash="h")
    assert entry is not None and entry["state"] == "pending"
    assert await store.complete("k", "t", {"goal_id": "gB"}, owner="B", body_hash="h") is True
    done = await store.claim("k", "t", owner="C", body_hash="h")
    assert done is not None and done["response"] == {"goal_id": "gB"}


async def test_slow_submission_keeps_its_claim_so_a_retry_does_not_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pending TTL 0.6 s, submission takes 1.5 s, retry at 0.9 s -> still one goal."""
    monkeypatch.setattr(idem_mod, "_PENDING_TTL_SECONDS", 0.6)
    monkeypatch.setattr(goals_api, "_IDEMPOTENCY_HEARTBEAT_SECONDS", 0.15)
    created: list[str] = []

    async def _slow_submit(**_kw: Any) -> dict[str, Any]:
        await asyncio.sleep(1.5)
        created.append("g")
        return {"goal_id": f"g{len(created)}", "status": "planning"}

    svc = AsyncMock()
    svc.submit_goal.side_effect = _slow_submit
    app = _app(svc, _store())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        first = asyncio.create_task(c.post("/goals", json={"goal": "slow"}, headers=_H))
        await asyncio.sleep(0.9)
        retry = await c.post("/goals", json={"goal": "slow"}, headers=_H)
        done = await first
    assert done.status_code == 202
    assert retry.status_code == 409
    assert created == ["g"]
