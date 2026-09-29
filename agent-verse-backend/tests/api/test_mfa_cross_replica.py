"""MFA across replicas: TOTP replay and pending enrollment must use Redis.

1. The Redis-backed replay check (``_check_totp_replay``) existed but every
   endpoint called the process-local ``_is_totp_replayed`` — a code accepted on
   replica A was accepted again on replica B.
2. With Redis wired, a Redis error must fail closed (503), not silently fall
   back to the process-local set.
3. The pending enrollment secret lived only in the process cache, so
   ``/enroll`` on one replica and ``/verify-enrollment`` on another failed.

A "replica" here is the same app with every piece of process-local MFA state
wiped between requests; only the shared (fake) Redis survives.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import mfa
from app.api.mfa import router as mfa_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TID = "tid-mfa-replica"
_CTX = TenantContext(tenant_id=TID, plan=PlanTier.PROFESSIONAL, api_key_id="kid-r")
_KEY = "ak_test_mfa_replica"
_H = {"X-API-Key": _KEY}


def _app(redis: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(mfa_router)
    app.state._redis = redis
    return app


def _wipe_process_state(*, keep_enabled_state: bool = True) -> None:
    """Forget everything a *different* replica would not have in memory.

    MFA enabled/secret lives in the DB in production (the cache stands in for
    it here), so it is kept unless asked otherwise.
    """
    entry = mfa._mfa_store.get(TID)
    mfa._used_totp_codes.pop(TID, None)
    mfa._rate_limits.pop(TID, None)
    mfa._mfa_store.pop(TID, None)
    if keep_enabled_state and entry is not None:
        mfa._mfa_store[TID] = {**entry, "pending_secret": None}


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    mfa._mfa_store.pop(TID, None)
    mfa._used_totp_codes.pop(TID, None)
    mfa._rate_limits.pop(TID, None)
    yield
    mfa._mfa_store.pop(TID, None)
    mfa._used_totp_codes.pop(TID, None)
    mfa._rate_limits.pop(TID, None)


def _enable(secret: str) -> None:
    mfa._mfa_store[TID] = {
        "enabled": True,
        "secret": secret,
        "pending_secret": None,
        "recovery_codes_hashed": [],
    }


def test_enroll_and_confirm_on_different_replicas() -> None:
    redis = fakeredis.FakeAsyncRedis()
    replica_a, replica_b = TestClient(_app(redis)), TestClient(_app(redis))

    r = replica_a.post("/auth/mfa/enroll", headers=_H)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    _wipe_process_state(keep_enabled_state=False)

    assert replica_b.get("/auth/mfa/status", headers=_H).json()["has_pending_enrollment"] is True
    code = pyotp.TOTP(secret).now()
    r = replica_b.post("/auth/mfa/verify-enrollment", headers=_H, json={"code": code})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "enabled"
    _wipe_process_state()
    assert replica_a.get("/auth/mfa/status", headers=_H).json()["has_pending_enrollment"] is False


def test_pending_secret_is_encrypted_and_expires_in_redis() -> None:
    import asyncio

    redis = fakeredis.FakeAsyncRedis()
    client = TestClient(_app(redis))
    secret = client.post("/auth/mfa/enroll", headers=_H).json()["secret"]

    async def _inspect() -> tuple[list[bytes], Any, int]:
        keys = await redis.keys("mfa:pending:*")
        return keys, await redis.get(keys[0]), await redis.ttl(keys[0])

    keys, raw, ttl = asyncio.run(_inspect())
    assert len(keys) == 1
    assert secret.encode() not in raw, "pending TOTP secret must not sit in Redis in clear"
    assert 0 < ttl <= mfa._PENDING_ENROLLMENT_TTL


@pytest.mark.parametrize("endpoint", ["/auth/mfa/verify", "/auth/mfa/regenerate"])
def test_totp_replay_is_rejected_on_another_replica(endpoint: str) -> None:
    redis = fakeredis.FakeAsyncRedis()
    secret = pyotp.random_base32()
    _enable(secret)
    code = pyotp.TOTP(secret).now()

    first = TestClient(_app(redis)).post("/auth/mfa/verify", headers=_H, json={"code": code})
    assert first.status_code == 200, first.text
    _wipe_process_state()

    replay = TestClient(_app(redis)).post(endpoint, headers=_H, json={"code": code})
    assert replay.status_code == 422, replay.text
    assert "already used" in replay.text


def test_replay_across_replicas_on_disable() -> None:
    redis = fakeredis.FakeAsyncRedis()
    secret = pyotp.random_base32()
    _enable(secret)
    code = pyotp.TOTP(secret).now()
    assert (
        TestClient(_app(redis)).post("/auth/mfa/verify", headers=_H, json={"code": code})
    ).status_code == 200
    _wipe_process_state()
    r = TestClient(_app(redis)).post("/auth/mfa/disable", headers=_H, json={"code": code})
    assert r.status_code == 422
    assert mfa._mfa_store[TID]["enabled"] is True


def _broken_redis() -> MagicMock:
    redis = MagicMock()
    redis.set = AsyncMock(side_effect=ConnectionError("redis down"))
    redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
    redis.delete = AsyncMock(side_effect=ConnectionError("redis down"))
    pipe = MagicMock()
    pipe.incr = AsyncMock()
    pipe.expire = AsyncMock()
    pipe.execute = AsyncMock(side_effect=ConnectionError("redis down"))
    redis.pipeline = MagicMock(return_value=pipe)
    return redis


def test_replay_check_fails_closed_when_redis_errors() -> None:
    secret = pyotp.random_base32()
    _enable(secret)
    r = TestClient(_app(_broken_redis())).post(
        "/auth/mfa/verify", headers=_H, json={"code": pyotp.TOTP(secret).now()}
    )
    assert r.status_code == 503, r.text
    assert "session_token" not in r.text


def test_enroll_fails_closed_when_redis_errors() -> None:
    r = TestClient(_app(_broken_redis())).post("/auth/mfa/enroll", headers=_H)
    assert r.status_code == 503, r.text
    assert "secret" not in r.json()


async def test_redis_replay_key_outlives_the_whole_acceptance_window() -> None:
    """valid_window=1 accepts a code for up to 3 TOTP steps; remember it that long."""
    redis = fakeredis.FakeAsyncRedis()
    request = MagicMock()
    request.app.state._redis = redis
    assert await mfa._check_totp_replay(TID, "123456", request) is False
    assert await mfa._check_totp_replay(TID, "123456", request) is True
    (key,) = await redis.keys("mfa:used_totp:*")
    assert await redis.ttl(key) >= 89


def test_in_process_replay_spans_adjacent_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    import time as _time

    now = [1_700_000_000.0]
    monkeypatch.setattr(_time, "time", lambda: now[0])
    assert mfa._is_totp_replayed(TID, "111111") is False
    now[0] += 30  # next TOTP step: the code is still accepted by valid_window=1
    assert mfa._is_totp_replayed(TID, "111111") is True
