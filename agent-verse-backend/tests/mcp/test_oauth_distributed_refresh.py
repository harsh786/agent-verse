"""OAUTH-02: refresh is serialised across replicas/workers, not just in-process.

Two OAuthFlowManager instances (two replicas) sharing one Redis and one token
store refresh the same expired token concurrently: the provider (which rotates
refresh tokens) must see exactly ONE refresh request, and both get the token.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
import respx

from app.mcp.oauth import OAuthFlowManager, OAuthToken, build_worker_oauth_manager

T = ("oauth-dist-t", "srv")
_TOKEN_URL = "https://auth.example.com/token"


class _SharedStore:
    """The oauth_tokens row both replicas read and write."""

    def __init__(self) -> None:
        self.row: tuple[Any, ...] = (
            "old-at",
            "rt-1",
            datetime.now(UTC) - timedelta(minutes=5),  # expired
            "Bearer",
            "",
        )

    async def read(self, tenant_id: str, server_id: str) -> Any:
        return self.row

    async def write(self, tenant_id: str, server_id: str, token: OAuthToken) -> None:
        await asyncio.sleep(0.05)  # a real write takes time: widen the race
        self.row = (
            token.access_token,
            token.refresh_token,
            datetime.now(UTC) + timedelta(seconds=token.expires_in),
            token.token_type,
            token.scope,
        )


def _replica(store: _SharedStore, redis: Any) -> OAuthFlowManager:
    mgr = OAuthFlowManager()
    mgr._db_session_factory = object()
    mgr._fetch_token_row = store.read  # type: ignore[method-assign]
    mgr._persist_token_to_db = store.write  # type: ignore[method-assign]
    mgr.set_redis(redis)
    return mgr


@pytest.mark.asyncio
async def test_two_replicas_refresh_once() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = _SharedStore()
    a, b = _replica(store, redis), _replica(store, redis)
    calls = 0

    def _issue(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if b"rt-1" not in request.content:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200, json={"access_token": "new-at", "refresh_token": "rt-2", "expires_in": 3600}
        )

    with respx.mock:
        respx.post(_TOKEN_URL).mock(side_effect=_issue)
        results = await asyncio.gather(
            a.refresh_token(server_id="srv", tenant_id=T[0], token_url=_TOKEN_URL),
            b.refresh_token(server_id="srv", tenant_id=T[0], token_url=_TOKEN_URL),
        )

    assert calls == 1
    assert [r.access_token if r else None for r in results] == ["new-at", "new-at"]
    assert store.row[1] == "rt-2"
    assert await redis.keys("oauth_refresh_lock:*") == []  # released


def test_worker_manager_gets_the_shared_redis() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    mgr = build_worker_oauth_manager(object(), redis=redis)
    assert mgr is not None and mgr._redis is redis
