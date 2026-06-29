"""Comprehensive tests for app/auth/scope_enforcement.py."""
from __future__ import annotations

from datetime import UTC, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request, Response
from starlette.testclient import TestClient

from app.auth.scope_enforcement import (
    ENDPOINT_SCOPES,
    EXEMPT_PATH_PREFIXES,
    ROLE_SCOPES,
    ABACEvaluator,
    RoleResolver,
    ScopeEnforcementMiddleware,
    _ALL_SCOPES,
)


# ---------------------------------------------------------------------------
# ENDPOINT_SCOPES registry
# ---------------------------------------------------------------------------


def test_endpoint_scopes_not_empty():
    assert len(ENDPOINT_SCOPES) > 0


def test_endpoint_scopes_goals_read():
    assert ENDPOINT_SCOPES[("GET", "/goals")] == "goals:read"


def test_endpoint_scopes_goals_write():
    assert ENDPOINT_SCOPES[("POST", "/goals")] == "goals:write"


def test_endpoint_scopes_goals_delete():
    assert ENDPOINT_SCOPES[("DELETE", "/goals")] == "goals:delete"


def test_endpoint_scopes_analytics_requires_audit_read():
    assert ENDPOINT_SCOPES[("GET", "/analytics")] == "audit:read"


def test_endpoint_scopes_tenancy_write():
    assert ENDPOINT_SCOPES[("PATCH", "/tenants/me")] == "tenancy:write"


# ---------------------------------------------------------------------------
# EXEMPT_PATH_PREFIXES
# ---------------------------------------------------------------------------


def test_exempt_paths_includes_health():
    assert "/health" in EXEMPT_PATH_PREFIXES


def test_exempt_paths_includes_docs():
    assert "/docs" in EXEMPT_PATH_PREFIXES


def test_exempt_paths_includes_auth():
    assert "/auth/" in EXEMPT_PATH_PREFIXES


def test_exempt_paths_includes_metrics():
    assert "/metrics" in EXEMPT_PATH_PREFIXES


# ---------------------------------------------------------------------------
# ROLE_SCOPES
# ---------------------------------------------------------------------------


def test_admin_role_has_all_scopes():
    assert ROLE_SCOPES["admin"] == _ALL_SCOPES


def test_operator_role_has_goals_execute():
    assert "goals:execute" in ROLE_SCOPES["operator"]


def test_operator_role_no_admin_scopes():
    assert "tenancy:write" not in ROLE_SCOPES["operator"]
    assert "audit:export" not in ROLE_SCOPES["operator"]


def test_viewer_role_read_only():
    for scope in ROLE_SCOPES["viewer"]:
        action = scope.split(":")[1]
        assert action == "read", f"viewer has non-read scope: {scope}"


def test_approver_role_has_governance_approve():
    assert "governance:approve" in ROLE_SCOPES["approver"]


def test_approver_role_no_write():
    assert "goals:write" not in ROLE_SCOPES["approver"]


# ---------------------------------------------------------------------------
# ABACEvaluator
# ---------------------------------------------------------------------------


async def test_abac_evaluate_empty_conditions_returns_true():
    ev = ABACEvaluator()
    result = await ev.evaluate({}, {}, {})
    assert result is True


async def test_abac_evaluate_department_match_true():
    ev = ABACEvaluator()
    result = await ev.evaluate(
        {"department_match": True},
        {"department": "Engineering"},
        {"department": "Engineering"},
    )
    assert result is True


async def test_abac_evaluate_department_match_false():
    ev = ABACEvaluator()
    result = await ev.evaluate(
        {"department_match": True},
        {"department": "Engineering"},
        {"department": "Marketing"},
    )
    assert result is False


async def test_abac_evaluate_ownership_creator_match():
    ev = ABACEvaluator()
    result = await ev.evaluate(
        {"ownership": "creator"},
        {"user_id": "user-123"},
        {"created_by": "user-123"},
    )
    assert result is True


async def test_abac_evaluate_ownership_creator_no_match():
    ev = ABACEvaluator()
    result = await ev.evaluate(
        {"ownership": "creator"},
        {"user_id": "user-123"},
        {"created_by": "user-456"},
    )
    assert result is False


async def test_abac_evaluate_time_window_within():
    ev = ABACEvaluator()
    result = await ev.evaluate(
        {"time_window": {"start": "00:00", "end": "23:59", "tz": "UTC"}},
        {},
        {},
    )
    assert result is True


async def test_abac_evaluate_time_window_outside():
    ev = ABACEvaluator()
    # A window in the past that never matches
    result = await ev.evaluate(
        {"time_window": {"start": "25:00", "end": "26:00", "tz": "UTC"}},
        {},
        {},
    )
    assert result is False


async def test_abac_evaluate_multiple_conditions_all_must_pass():
    ev = ABACEvaluator()
    # Department matches but ownership doesn't
    result = await ev.evaluate(
        {"department_match": True, "ownership": "creator"},
        {"department": "Eng", "user_id": "u1"},
        {"department": "Eng", "created_by": "u2"},
    )
    assert result is False


# ---------------------------------------------------------------------------
# RoleResolver
# ---------------------------------------------------------------------------


async def test_role_resolver_simple_role():
    from unittest.mock import AsyncMock, MagicMock

    role_mock = MagicMock()
    role_mock.permissions = ["goals:read", "goals:write"]
    role_mock.parent_role_id = None

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = role_mock
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(return_value=result_mock)

    resolver = RoleResolver()
    perms = await resolver.resolve("role-id-1", db_mock)
    assert "goals:read" in perms
    assert "goals:write" in perms


async def test_role_resolver_role_not_found():
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(return_value=result_mock)

    resolver = RoleResolver()
    perms = await resolver.resolve("nonexistent-role", db_mock)
    assert perms == set()


async def test_role_resolver_cycle_guard():
    """A role that references itself should not infinite loop."""
    role_mock = MagicMock()
    role_mock.permissions = ["goals:read"]
    role_mock.parent_role_id = "role-id-1"  # Self-reference

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = role_mock
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(return_value=result_mock)

    resolver = RoleResolver()
    perms = await resolver.resolve("role-id-1", db_mock)
    # Should return without infinite recursion
    assert "goals:read" in perms


# ---------------------------------------------------------------------------
# ScopeEnforcementMiddleware._required_scope
# ---------------------------------------------------------------------------


def test_required_scope_exact_match():
    assert ScopeEnforcementMiddleware._required_scope("GET", "/goals") == "goals:read"


def test_required_scope_prefix_match():
    # /goals/some-uuid should match /goals prefix
    assert ScopeEnforcementMiddleware._required_scope("GET", "/goals/abc-123") == "goals:read"


def test_required_scope_delete_prefix():
    assert ScopeEnforcementMiddleware._required_scope("DELETE", "/goals/abc") == "goals:delete"


def test_required_scope_unregistered_returns_none():
    assert ScopeEnforcementMiddleware._required_scope("GET", "/unregistered-path") is None


def test_required_scope_agents():
    assert ScopeEnforcementMiddleware._required_scope("POST", "/agents") == "agents:write"


def test_required_scope_connectors_read():
    assert ScopeEnforcementMiddleware._required_scope("GET", "/connectors") == "mcp:read"


def test_required_scope_governance_read():
    assert ScopeEnforcementMiddleware._required_scope("GET", "/governance") == "governance:read"


def test_required_scope_longer_prefix_wins():
    # /tenants/me is more specific than /tenants
    assert ScopeEnforcementMiddleware._required_scope("GET", "/tenants/me") == "tenancy:read"


# ---------------------------------------------------------------------------
# ScopeEnforcementMiddleware._client_ip
# ---------------------------------------------------------------------------


def test_client_ip_from_x_forwarded_for():
    request = MagicMock()
    request.headers = {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    ip = ScopeEnforcementMiddleware._client_ip(request)
    assert ip == "1.2.3.4"


def test_client_ip_from_x_real_ip():
    request = MagicMock()
    request.headers = {"X-Real-IP": "5.6.7.8"}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"

    ip = ScopeEnforcementMiddleware._client_ip(request)
    assert ip == "5.6.7.8"


def test_client_ip_fallback_to_client_host():
    request = MagicMock()
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "192.168.1.5"

    ip = ScopeEnforcementMiddleware._client_ip(request)
    assert ip == "192.168.1.5"


def test_client_ip_no_client_returns_default():
    request = MagicMock()
    request.headers = {}
    request.client = None

    ip = ScopeEnforcementMiddleware._client_ip(request)
    assert ip == "0.0.0.0"


# ---------------------------------------------------------------------------
# ScopeEnforcementMiddleware._load_scopes
# ---------------------------------------------------------------------------


async def test_load_scopes_role_fallback_admin():
    scopes = await ScopeEnforcementMiddleware._load_scopes(
        db_factory=None,
        tenant_id="t1",
        key_id="k1",
        roles=("admin",),
    )
    assert "goals:read" in scopes
    assert "tenancy:write" in scopes
    assert "governance:approve" in scopes


async def test_load_scopes_role_fallback_viewer():
    scopes = await ScopeEnforcementMiddleware._load_scopes(
        db_factory=None,
        tenant_id="t1",
        key_id="k1",
        roles=("viewer",),
    )
    assert "goals:read" in scopes
    assert "goals:write" not in scopes


async def test_load_scopes_empty_roles_returns_empty():
    scopes = await ScopeEnforcementMiddleware._load_scopes(
        db_factory=None,
        tenant_id="t1",
        key_id="k1",
        roles=(),
    )
    assert scopes == set()


async def test_load_scopes_multiple_roles_merged():
    scopes = await ScopeEnforcementMiddleware._load_scopes(
        db_factory=None,
        tenant_id="t1",
        key_id="k1",
        roles=("viewer", "approver"),
    )
    # Should include both viewer and approver scopes
    assert "goals:read" in scopes
    assert "governance:approve" in scopes


# ---------------------------------------------------------------------------
# ScopeEnforcementMiddleware.dispatch — exempt paths
# ---------------------------------------------------------------------------


def _make_test_app(redis=None, tenant=None, db_factory=None) -> FastAPI:
    """Create a minimal FastAPI app with ScopeEnforcementMiddleware."""
    fast_app = FastAPI()
    fast_app.add_middleware(ScopeEnforcementMiddleware)

    if redis is not None:
        fast_app.state._rate_limiter_redis = redis

    if tenant is not None:
        tenant_svc = MagicMock()
        if db_factory:
            tenant_svc._db = db_factory
        fast_app.state.tenant_service = tenant_svc

    @fast_app.get("/health")
    async def health():
        return {"status": "ok"}

    @fast_app.get("/goals")
    async def goals(request: Request):
        return {"goals": []}

    return fast_app


async def test_dispatch_exempt_health_path():
    """Requests to /health should bypass scope enforcement entirely."""
    from httpx import ASGITransport, AsyncClient

    fast_app = _make_test_app()

    async with AsyncClient(transport=ASGITransport(app=fast_app), base_url="http://test") as c:
        resp = await c.get("/health")

    assert resp.status_code == 200


async def test_dispatch_no_tenant_context_passes_through():
    """Without tenant context, ScopeMiddleware defers to TenantMiddleware (safety net)."""
    from httpx import ASGITransport, AsyncClient

    fast_app = FastAPI()
    fast_app.add_middleware(ScopeEnforcementMiddleware)

    @fast_app.get("/goals")
    async def goals():
        return {"goals": []}

    async with AsyncClient(transport=ASGITransport(app=fast_app), base_url="http://test") as c:
        resp = await c.get("/goals")
    # No tenant → passes to next handler (returns 200 since no TenantMiddleware here)
    assert resp.status_code == 200


async def test_dispatch_no_roles_passes_through():
    """Keys with no role assignments are legacy — pass through without scope check."""
    from httpx import ASGITransport, AsyncClient
    from app.tenancy.context import PlanTier, TenantContext

    fast_app = FastAPI()
    fast_app.add_middleware(ScopeEnforcementMiddleware)

    @fast_app.get("/goals")
    async def goals(request: Request):
        return {"goals": []}

    # Inject a tenant with no roles
    @fast_app.middleware("http")
    async def inject_tenant(request: Request, call_next):
        request.state.tenant = TenantContext(
            tenant_id="t1",
            plan=PlanTier.FREE,
            api_key_id="k1",
        )
        return await call_next(request)

    async with AsyncClient(transport=ASGITransport(app=fast_app), base_url="http://test") as c:
        resp = await c.get("/goals")
    assert resp.status_code == 200


async def test_dispatch_scope_denied_returns_403():
    """A key with viewer role should be denied access to goals:write (POST /goals)."""
    from httpx import ASGITransport, AsyncClient
    from app.tenancy.context import PlanTier, TenantContext

    fast_app = FastAPI()
    fast_app.add_middleware(ScopeEnforcementMiddleware)

    @fast_app.post("/goals")
    async def create_goal(request: Request):
        return {"goal_id": "new-goal"}

    @fast_app.middleware("http")
    async def inject_viewer_tenant(request: Request, call_next):
        # roles kwarg supported by TenantContext constructor
        ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1",
                            roles=("viewer",))
        request.state.tenant = ctx
        return await call_next(request)

    async with AsyncClient(transport=ASGITransport(app=fast_app), base_url="http://test") as c:
        resp = await c.post("/goals", json={"goal": "test"})

    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "INSUFFICIENT_SCOPE"
    assert "goals:write" in body["required_scope"]


async def test_dispatch_scope_granted_returns_200():
    """A key with operator role should be allowed to POST /goals."""
    from httpx import ASGITransport, AsyncClient
    from app.tenancy.context import PlanTier, TenantContext

    fast_app = FastAPI()
    fast_app.add_middleware(ScopeEnforcementMiddleware)

    @fast_app.post("/goals")
    async def create_goal(request: Request):
        return {"goal_id": "new-goal"}

    @fast_app.middleware("http")
    async def inject_operator_tenant(request: Request, call_next):
        ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1",
                            roles=("operator",))
        request.state.tenant = ctx
        return await call_next(request)

    async with AsyncClient(transport=ASGITransport(app=fast_app), base_url="http://test") as c:
        resp = await c.post("/goals", json={"goal": "test"})

    assert resp.status_code == 200


async def test_dispatch_ip_blocked_returns_403():
    """A request from a blocked IP should return 403 IP_NOT_ALLOWED."""
    from httpx import ASGITransport, AsyncClient
    from app.tenancy.context import PlanTier, TenantContext

    fast_app = FastAPI()
    fast_app.add_middleware(ScopeEnforcementMiddleware)

    @fast_app.get("/goals")
    async def goals(request: Request):
        return {"goals": []}

    @fast_app.middleware("http")
    async def inject_tenant_with_ip(request: Request, call_next):
        ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k1",
                            roles=("admin",))
        request.state.tenant = ctx
        return await call_next(request)

    redis_mock = AsyncMock()
    # IP cache returns a restrictive CIDR
    redis_mock.get = AsyncMock(return_value='["10.0.0.0/8"]')
    fast_app.state._rate_limiter_redis = redis_mock

    # X-Forwarded-For from a blocked IP
    async with AsyncClient(transport=ASGITransport(app=fast_app), base_url="http://test") as c:
        resp = await c.get("/goals", headers={"X-Forwarded-For": "1.2.3.4"})

    assert resp.status_code == 403
    assert resp.json()["error"] == "IP_NOT_ALLOWED"
