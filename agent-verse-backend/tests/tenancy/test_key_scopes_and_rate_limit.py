"""Regression tests: API-key scopes reach the auth context; rate limit is per tenant.

1. Scopes requested when an API key is created were stored on the key but never
   reached ``TenantContext``: ``resolve_api_key`` built the context from the key's
   *roles* only, so a key created with ``scopes=["goals:read"]`` resolved to a
   plain ``operator`` and could write goals, agents, connectors, ...
   (``ScopeEnforcementMiddleware._load_scopes`` reads ``api_key_scopes``, which no
   code path writes.)

5. The plan rate limit was bucketed by URL path (``check_and_record(path, ...)``),
   so a tenant got a fresh 60 rpm bucket for every distinct path — ``/goals/1``,
   ``/goals/2``, ... — multiplying its quota without bound.
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.auth.scope_enforcement import ScopeEnforcementMiddleware
from app.services.tenant_service import TenantService, _hash_key
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

# ── helpers ───────────────────────────────────────────────────────────────────


def _app(svc: TenantService, *, redis: Any = None) -> FastAPI:
    app = FastAPI()
    app.state.tenant_service = svc
    app.state._rate_limiter_redis = redis
    app.add_middleware(ScopeEnforcementMiddleware)
    app.add_middleware(TenantMiddleware, key_resolver=svc.resolve_api_key)

    @app.get("/goals")
    async def list_goals() -> dict[str, str]:
        return {"ok": "read"}

    @app.post("/goals")
    async def create_goal() -> dict[str, str]:
        return {"ok": "write"}

    @app.post("/agents")
    async def create_agent() -> dict[str, str]:
        return {"ok": "agent"}

    @app.get("/goals/{goal_id}")
    async def get_goal(goal_id: str, request: Request) -> dict[str, str]:
        return {"id": goal_id}

    return app


async def _tenant_with_key(svc: TenantService, scopes: list[str]) -> tuple[str, str]:
    t = await svc.create_tenant(name="Acme", email=f"a{len(svc._tenants)}@acme.test")
    key = await svc.create_api_key(tenant_id=t["tenant_id"], name="narrow", scopes=scopes)
    return t["tenant_id"], key["raw_key"]


# ── 1. key scopes → TenantContext → enforcement ───────────────────────────────


@pytest.mark.asyncio
async def test_resolved_context_carries_the_keys_scopes() -> None:
    svc = TenantService()
    _, raw = await _tenant_with_key(svc, ["goals:read"])
    ctx = await svc.resolve_api_key(raw)
    assert ctx is not None
    assert ctx.scopes == ("goals:read",)


@pytest.mark.asyncio
async def test_redis_cached_context_keeps_scopes() -> None:
    svc = TenantService()
    svc.set_redis(fakeredis.FakeAsyncRedis())
    _, raw = await _tenant_with_key(svc, ["goals:read"])
    first = await svc.resolve_api_key(raw)  # populates the cache
    second = await svc.resolve_api_key(raw)  # served from the cache
    assert first is not None and second is not None
    assert second.scopes == ("goals:read",)


@pytest.mark.asyncio
async def test_pre_fix_cache_entry_without_scopes_is_not_trusted() -> None:
    """An ``api_key:{hash}`` entry written before this fix has no ``scopes``
    field; trusting it would treat a narrow key as unrestricted for 300 s."""
    redis = fakeredis.FakeAsyncRedis()
    svc = TenantService()
    svc.set_redis(redis)
    tid, raw = await _tenant_with_key(svc, ["goals:read"])
    await redis.set(
        f"api_key:{_hash_key(raw)}",
        json.dumps({"tenant_id": tid, "plan": "free", "api_key_id": "x", "roles": ["operator"]}),
    )
    ctx = await svc.resolve_api_key(raw)
    assert ctx is not None
    assert ctx.scopes == ("goals:read",)


@pytest.mark.asyncio
async def test_narrow_key_cannot_use_its_roles_other_scopes() -> None:
    svc = TenantService()
    _, raw = await _tenant_with_key(svc, ["goals:read"])
    client = TestClient(_app(svc), raise_server_exceptions=False)
    h = {"X-API-Key": raw}

    assert client.get("/goals", headers=h).status_code == 200
    # operator role grants goals:write / agents:write — the key's own scopes do not.
    denied = client.post("/goals", headers=h)
    assert denied.status_code == 403
    assert denied.json()["error"] == "INSUFFICIENT_SCOPE"
    assert denied.json()["required_scope"] == "goals:write"
    assert client.post("/agents", headers=h).status_code == 403


@pytest.mark.asyncio
async def test_key_scope_beyond_role_is_still_denied() -> None:
    """Effective scopes are the INTERSECTION of the key's scopes and its roles'."""
    svc = TenantService()
    _, raw = await _tenant_with_key(svc, ["goals:read", "costs:admin"])
    ctx = await svc.resolve_api_key(raw)
    assert ctx is not None and ctx.roles == ("operator",)
    app = _app(svc)

    @app.post("/costs/budgets")
    async def set_budget() -> dict[str, str]:
        return {"ok": "budget"}

    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/costs/budgets", headers={"X-API-Key": raw}).status_code == 403


@pytest.mark.asyncio
async def test_unscoped_key_keeps_role_behaviour() -> None:
    svc = TenantService()
    _, raw = await _tenant_with_key(svc, [])
    client = TestClient(_app(svc), raise_server_exceptions=False)
    h = {"X-API-Key": raw}
    assert client.get("/goals", headers=h).status_code == 200
    assert client.post("/goals", headers=h).status_code == 200


def test_tenant_context_scopes_default_empty() -> None:
    ctx = TenantContext(tenant_id="t", plan=PlanTier.FREE, api_key_id="k")
    assert ctx.scopes == ()


# ── 5. rate limit keyed by tenant, not path ───────────────────────────────────


@pytest.mark.asyncio
async def test_varying_the_path_does_not_multiply_the_quota() -> None:
    svc = TenantService()
    _, raw = await _tenant_with_key(svc, [])
    client = TestClient(_app(svc, redis=fakeredis.FakeAsyncRedis()))
    h = {"X-API-Key": raw}
    limit = 60  # FREE plan requests_per_minute
    statuses = [client.get(f"/goals/g{i}", headers=h).status_code for i in range(limit)]
    assert statuses == [200] * limit
    over = client.get("/goals/one-more-new-path", headers=h)
    assert over.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_buckets_are_isolated_per_tenant() -> None:
    svc = TenantService()
    _, raw_a = await _tenant_with_key(svc, [])
    _, raw_b = await _tenant_with_key(svc, [])
    client = TestClient(_app(svc, redis=fakeredis.FakeAsyncRedis()))
    for i in range(60):
        client.get(f"/goals/a{i}", headers={"X-API-Key": raw_a})
    assert client.get("/goals/x", headers={"X-API-Key": raw_a}).status_code == 429
    assert client.get("/goals/x", headers={"X-API-Key": raw_b}).status_code == 200
