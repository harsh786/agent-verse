"""Idempotency-Key semantics on POST /goals.

Regressions:
* the key was claimed BEFORE validation and never released, so a submission
  rejected with 422/429 made every retry with the same key a 409 (with no
  goal_id) for an hour;
* a successful submission's replay answered 409 without the goal it created;
* any error in the idempotency store was swallowed (fail open), silently
  dropping the duplicate protection.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.reliability.idempotency import IdempotencyStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-idem", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_KEY = "ak_test_idem"
_H = {"X-API-Key": _KEY, "Idempotency-Key": "idem-123"}


def _app(svc: Any, store: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(goals_router)
    app.state.goal_service = svc
    app.state.idempotency_store = store
    return app


def _store() -> IdempotencyStore:
    return IdempotencyStore(fakeredis.aioredis.FakeRedis(decode_responses=True))


def test_rejected_submission_releases_the_key_so_a_retry_succeeds() -> None:
    svc = AsyncMock()
    svc.submit_goal.side_effect = [
        HTTPException(status_code=429, detail="concurrency limit"),
        {"goal_id": "g-after-retry", "status": "planning"},
    ]
    client = TestClient(_app(svc, _store()), raise_server_exceptions=False)

    first = client.post("/goals", json={"goal": "do it"}, headers=_H)
    assert first.status_code == 429

    retry = client.post("/goals", json={"goal": "do it"}, headers=_H)
    assert retry.status_code == 202, retry.text
    assert retry.json()["goal_id"] == "g-after-retry"


def test_validation_failure_releases_the_key() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {"goal_id": "g-ok", "status": "planning"}
    client = TestClient(_app(svc, _store()), raise_server_exceptions=False)

    class _Registry:
        def resolve(self, name: str) -> Any:
            raise LookupError(name)

    client.app.state.strategy_registry = _Registry()  # type: ignore[attr-defined]
    bad = client.post(
        "/goals", json={"goal": "do it", "strategy_override": "nope"}, headers=_H
    )
    assert bad.status_code == 422

    good = client.post("/goals", json={"goal": "do it"}, headers=_H)
    assert good.status_code == 202, good.text
    assert good.json()["goal_id"] == "g-ok"


def test_replay_of_a_successful_submission_returns_its_goal_id() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {"goal_id": "g-first", "status": "planning"}
    client = TestClient(_app(svc, _store()), raise_server_exceptions=False)

    first = client.post("/goals", json={"goal": "do it"}, headers=_H)
    assert first.status_code == 202
    replay = client.post("/goals", json={"goal": "do it"}, headers=_H)

    assert replay.status_code == 202, replay.text
    assert replay.json()["goal_id"] == "g-first"
    assert replay.json()["idempotent_replay"] is True
    assert svc.submit_goal.await_count == 1, "the replay must not create a second goal"


def test_in_flight_duplicate_is_a_conflict() -> None:
    # Another request holds the claim but has not finished yet.
    store = AsyncMock()
    store.claim.return_value = {"state": "pending"}
    svc = AsyncMock()
    client = TestClient(_app(svc, store), raise_server_exceptions=False)

    resp = client.post("/goals", json={"goal": "do it"}, headers=_H)
    assert resp.status_code == 409
    svc.submit_goal.assert_not_awaited()


def test_idempotency_store_error_fails_closed_with_503() -> None:
    broken = AsyncMock()
    broken.claim.side_effect = ConnectionError("redis down")
    svc = AsyncMock()
    client = TestClient(_app(svc, broken), raise_server_exceptions=False)

    resp = client.post("/goals", json={"goal": "do it"}, headers=_H)

    assert resp.status_code == 503
    svc.submit_goal.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_claim_complete_release_roundtrip() -> None:
    store = _store()
    assert await store.claim("k", "t") is None
    assert await store.claim("k", "t") == {"state": "pending"}
    await store.complete("k", "t", {"goal_id": "g1"})
    assert await store.claim("k", "t") == {"state": "done", "response": {"goal_id": "g1"}}
    await store.release("k", "t")
    assert await store.claim("k", "t") is None
