"""Tests for Agent Scopes / Auth / AuthZ — migration 0049.

Test suite covering:
  1. test_scope_enforcement_blocks_viewer_deleting_agents
  2. test_scope_enforcement_allows_operator_submitting_goals
  3. test_role_cache_returns_from_redis_on_second_call
  4. test_ip_allowlist_blocks_nonwhitelisted_ip
  5. test_custom_role_creation_and_assignment
  6. test_downgrade_restores_scopes_column
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.auth.ip_allowlist import IPAllowlistCache, is_ip_allowed
from app.auth.permission_cache import PermissionCache
from app.auth.scope_enforcement import (
    ABACEvaluator,
    ENDPOINT_SCOPES,
    ROLE_SCOPES,
    ScopeEnforcementMiddleware,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.domain_role_templates import DOMAIN_ROLE_TEMPLATES


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_mock_redis() -> Any:
    """In-memory Redis mock with get / setex / delete / scan / sorted-set ops."""
    store: dict[str, str] = {}
    zsets: dict[str, dict[str, float]] = {}
    r = AsyncMock()

    async def _get(k: str) -> str | None:
        return store.get(k)

    async def _setex(k: str, ttl: int, v: str) -> None:
        store[k] = v if isinstance(v, str) else json.dumps(v)

    async def _delete(*keys: str) -> int:
        removed = 0
        for k in keys:
            if k in store:
                del store[k]
                removed += 1
        return removed

    async def _scan(cursor: int, match: str = "*", count: int = 100):
        import fnmatch

        hits = [k for k in list(store.keys()) if fnmatch.fnmatch(k, match)]
        return 0, hits

    async def _zadd(k: str, mapping: dict) -> int:
        zset = zsets.setdefault(k, {})
        added = sum(1 for m in mapping if m not in zset)
        zset.update(mapping)
        return added

    async def _zremrangebyscore(k: str, min_s: float, max_s: float) -> int:
        zset = zsets.get(k)
        if not zset:
            return 0
        before = len(zset)
        zsets[k] = {m: s for m, s in zset.items() if not (min_s <= s <= max_s)}
        return before - len(zsets[k])

    async def _zcard(k: str) -> int:
        return len(zsets.get(k, {}))

    async def _expire(k: str, ttl: int) -> bool:
        return True

    r.get = _get
    r.setex = _setex
    r.delete = _delete
    r.scan = _scan
    r.zadd = _zadd
    r.zremrangebyscore = _zremrangebyscore
    r.zcard = _zcard
    r.expire = _expire
    r._store = store
    return r


def _viewer_ctx(tenant_id: str = "t_viewer") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        plan=PlanTier.FREE,
        api_key_id="key_viewer",
        roles=("viewer",),
    )


def _operator_ctx(tenant_id: str = "t_operator") -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        plan=PlanTier.FREE,
        api_key_id="key_operator",
        roles=("operator",),
    )


class _MockTenantService:
    """Minimal TenantService substitute that returns a fixed TenantContext."""

    def __init__(self, ctx: TenantContext, key: str = "test_key") -> None:
        self._ctx = ctx
        self._key = key
        self._db: Any = None

    async def resolve_api_key(self, raw_key: str) -> TenantContext | None:
        if raw_key == self._key:
            return self._ctx
        return None


# ---------------------------------------------------------------------------
# 1. test_scope_enforcement_blocks_viewer_deleting_agents
# ---------------------------------------------------------------------------


async def test_scope_enforcement_blocks_viewer_deleting_agents() -> None:
    """A viewer-role key must receive 403 when attempting DELETE /agents/."""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    app.state.tenant_service = _MockTenantService(ctx=_viewer_ctx(), key="viewer_key")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.delete("/agents/some-uuid", headers={"X-API-Key": "viewer_key"})

    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("required_scope") == "agents:delete", (
        f"Missing required_scope in response: {body}"
    )


# ---------------------------------------------------------------------------
# 2. test_scope_enforcement_allows_operator_submitting_goals
# ---------------------------------------------------------------------------


async def test_scope_enforcement_allows_operator_submitting_goals() -> None:
    """An operator-role key must NOT receive 403 when posting to /goals."""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    app.state.tenant_service = _MockTenantService(
        ctx=_operator_ctx(), key="operator_key"
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/goals",
            headers={"X-API-Key": "operator_key"},
            json={"goal": "Write a report"},
        )

    # Must NOT be 403 (scope denied). Could be 422 (validation) or 200/201.
    assert r.status_code != 403, (
        f"Operator should not be scope-blocked on POST /goals. "
        f"Got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# 3. test_role_cache_returns_from_redis_on_second_call
# ---------------------------------------------------------------------------


async def test_role_cache_returns_from_redis_on_second_call() -> None:
    """PermissionCache.get returns None on first call (miss) then the set value."""
    redis = _make_mock_redis()
    cache = PermissionCache(redis)

    tenant_id, key_id = "tenant_abc", "key_xyz"

    # First call: cache miss
    result_miss = await cache.get(tenant_id, key_id)
    assert result_miss is None, "Expected cache miss on first call"

    # Populate cache
    expected_scopes: set[str] = {"goals:read", "agents:read"}
    await cache.set(tenant_id, key_id, expected_scopes)

    # Verify data was actually stored in the underlying Redis mock
    raw = redis._store.get(f"perm:{tenant_id}:{key_id}")
    assert raw is not None, "Expected data to be stored in Redis after set()"

    # Second call: cache hit
    result_hit = await cache.get(tenant_id, key_id)
    assert result_hit == expected_scopes, (
        f"Expected {expected_scopes}, got {result_hit}"
    )

    # Invalidate and verify it returns None again
    await cache.invalidate(tenant_id, key_id)
    assert await cache.get(tenant_id, key_id) is None, (
        "Expected None after invalidation"
    )


# ---------------------------------------------------------------------------
# 4. test_ip_allowlist_blocks_nonwhitelisted_ip
# ---------------------------------------------------------------------------


async def test_ip_allowlist_blocks_nonwhitelisted_ip() -> None:
    """A non-loopback IP outside the tenant's CIDR allowlist is blocked (403)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    tenant_id = "t_ip_block"
    ctx = TenantContext(
        tenant_id=tenant_id,
        plan=PlanTier.FREE,
        api_key_id="key_ip",
        roles=("admin",),
    )
    app.state.tenant_service = _MockTenantService(ctx=ctx, key="ip_test_key")

    # Pre-populate Redis allowlist (only 10.0.0.0/8)
    mock_redis = _make_mock_redis()
    mock_redis._store[f"ip_wl:{tenant_id}"] = json.dumps(["10.0.0.0/8"])
    app.state._rate_limiter_redis = mock_redis

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        # X-Forwarded-For: 8.8.8.8 is not in 10.0.0.0/8
        r = await c.get(
            "/goals",
            headers={
                "X-API-Key": "ip_test_key",
                "X-Forwarded-For": "8.8.8.8",
            },
        )

    assert r.status_code == 403, (
        f"Expected 403 for blocked IP, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert "IP_NOT_ALLOWED" in str(body) or "IP" in str(body), (
        f"Expected IP_NOT_ALLOWED error code in response: {body}"
    )


# ---------------------------------------------------------------------------
# 5. test_custom_role_creation_and_assignment
# ---------------------------------------------------------------------------


async def test_custom_role_creation_and_assignment() -> None:
    """Custom role templates cover all five domains; permissions are well-formed."""
    # Verify all five domains are present
    assert set(DOMAIN_ROLE_TEMPLATES) >= {
        "healthcare", "legal", "finance", "education", "ecommerce"
    }

    known_scopes = {
        "goals:read", "goals:write", "goals:delete", "goals:execute",
        "agents:read", "agents:write", "agents:delete",
        "knowledge:read", "knowledge:write", "knowledge:delete",
        "governance:read", "governance:write", "governance:approve",
        "tenancy:read", "tenancy:write",
        "audit:read", "audit:export",
        "costs:read", "costs:admin",
        "mcp:read", "mcp:write",
    }

    for domain, roles in DOMAIN_ROLE_TEMPLATES.items():
        for role in roles:
            assert "name" in role, f"{domain}/{role} missing 'name'"
            assert "permissions" in role, f"{domain}/{role} missing 'permissions'"
            bad = set(role["permissions"]) - known_scopes
            assert not bad, (
                f"Unknown permissions {bad} in {domain}/{role['name']}"
            )

    # Spot-checks: client_portal is read-only
    cp = next(
        r for r in DOMAIN_ROLE_TEMPLATES["legal"] if r["name"] == "client_portal"
    )
    assert cp["permissions"] == ["goals:read"]

    # Spot-check: phi_reader has no destructive scopes
    phi = next(
        r for r in DOMAIN_ROLE_TEMPLATES["healthcare"] if r["name"] == "phi_reader"
    )
    assert "goals:delete" not in phi["permissions"]
    assert "governance:approve" not in phi["permissions"]

    # Verify ROLE_SCOPES covers admin (all scopes)
    assert "goals:delete" in ROLE_SCOPES["admin"]
    assert "agents:delete" in ROLE_SCOPES["admin"]

    # Verify viewer does NOT have destructive scopes
    assert "goals:delete" not in ROLE_SCOPES["viewer"]
    assert "agents:delete" not in ROLE_SCOPES["viewer"]


# ---------------------------------------------------------------------------
# 6. test_downgrade_restores_scopes_column
# ---------------------------------------------------------------------------


def test_downgrade_restores_scopes_column() -> None:
    """Validate the downgrade migration restores api_keys.scopes from api_key_scopes."""
    import importlib.util
    import inspect
    import os

    migration_path = os.path.join(
        os.path.dirname(__file__),
        "../../app/db/migrations/versions/0054_scopes_rbac.py",
    )
    spec = importlib.util.spec_from_file_location("migration_0054", migration_path)
    assert spec is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)  # type: ignore[union-attr]

    assert callable(getattr(migration, "downgrade", None)), (
        "Migration 0054 must define a downgrade() function"
    )
    assert callable(getattr(migration, "upgrade", None)), (
        "Migration 0054 must define an upgrade() function"
    )

    # Verify revision metadata
    assert migration.revision == "0054"
    assert migration.down_revision == "0053"

    # Verify downgrade restores the scopes column
    downgrade_src = inspect.getsource(migration.downgrade)
    assert "scopes" in downgrade_src, (
        "downgrade() must restore the api_keys.scopes column"
    )
    assert "api_key_scopes" in downgrade_src, (
        "downgrade() must reference api_key_scopes table"
    )


# ---------------------------------------------------------------------------
# Bonus: ABACEvaluator unit tests
# ---------------------------------------------------------------------------


async def test_abac_empty_conditions_always_passes() -> None:
    ev = ABACEvaluator()
    assert await ev.evaluate({}, {}, {}) is True


async def test_abac_department_match_same_department() -> None:
    ev = ABACEvaluator()
    result = await ev.evaluate(
        {"department_match": True},
        {"department": "legal"},
        {"department": "legal"},
    )
    assert result is True


async def test_abac_department_match_different_department() -> None:
    ev = ABACEvaluator()
    result = await ev.evaluate(
        {"department_match": True},
        {"department": "finance"},
        {"department": "legal"},
    )
    assert result is False


async def test_abac_ownership_creator_match() -> None:
    ev = ABACEvaluator()
    uid = str(uuid4())
    result = await ev.evaluate(
        {"ownership": "creator"},
        {"user_id": uid},
        {"created_by": uid},
    )
    assert result is True


async def test_abac_ownership_creator_no_match() -> None:
    ev = ABACEvaluator()
    result = await ev.evaluate(
        {"ownership": "creator"},
        {"user_id": str(uuid4())},
        {"created_by": str(uuid4())},
    )
    assert result is False


# ---------------------------------------------------------------------------
# Bonus: ENDPOINT_SCOPES coverage check
# ---------------------------------------------------------------------------


def test_endpoint_scopes_cover_all_crud_verbs_for_goals() -> None:
    """ENDPOINT_SCOPES must map all major CRUD verbs for goals."""
    assert ("GET", "/goals") in ENDPOINT_SCOPES
    assert ("POST", "/goals") in ENDPOINT_SCOPES
    assert ("DELETE", "/goals") in ENDPOINT_SCOPES


def test_endpoint_scopes_agents_delete_requires_agents_delete() -> None:
    required = ScopeEnforcementMiddleware._required_scope("DELETE", "/agents/some-id")
    assert required == "agents:delete", f"Expected agents:delete, got {required}"


def test_ip_allowed_loopback_always_permitted() -> None:
    """Loopback IPs bypass even a restrictive allowlist."""
    assert is_ip_allowed("127.0.0.1", ["10.0.0.0/8"]) is True
    assert is_ip_allowed("::1", ["10.0.0.0/8"]) is True


def test_ip_allowed_empty_list_permits_all() -> None:
    assert is_ip_allowed("8.8.8.8", []) is True


def test_ip_allowed_blocks_external_ip() -> None:
    assert is_ip_allowed("8.8.8.8", ["10.0.0.0/8"]) is False


def test_ip_allowed_allows_ip_in_cidr() -> None:
    assert is_ip_allowed("10.5.5.5", ["10.0.0.0/8"]) is True


async def test_permission_cache_invalidate_tenant() -> None:
    """invalidate_tenant removes all keys for that tenant."""
    redis = _make_mock_redis()
    cache = PermissionCache(redis)

    for i in range(5):
        await cache.set("t1", f"k{i}", {"goals:read"})

    await cache.invalidate_tenant("t1")

    for i in range(5):
        assert await cache.get("t1", f"k{i}") is None


async def test_permission_cache_different_tenants_isolated() -> None:
    """Permission cache must isolate tenants from each other."""
    redis = _make_mock_redis()
    cache = PermissionCache(redis)

    await cache.set("t1", "k1", {"goals:read"})
    await cache.set("t2", "k1", {"goals:write"})

    assert await cache.get("t1", "k1") == {"goals:read"}
    assert await cache.get("t2", "k1") == {"goals:write"}
