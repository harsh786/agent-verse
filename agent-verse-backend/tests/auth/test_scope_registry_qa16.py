"""QA-16: scope names are real and every advertised endpoint has a scope.

(a) Keys could be minted with scope names the backend never checks
    (``connectors:read``, ``analytics:read``, ``mcp:*`` …) — the key then failed
    every request. Unknown scopes are now rejected with 422 (the middleware
    matches scopes exactly, so wildcard forms are unknown too).
(b) /models and the /api/v1 workflow-engine routes had no ENDPOINT_SCOPES
    entry, so a key minted with explicit scopes was always refused there
    ("no scope that an API key with explicit scopes can hold").
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.auth.scope_enforcement import _ALL_SCOPES, ENDPOINT_SCOPES, ScopeEnforcementMiddleware
from app.services.tenant_service import TenantService
from app.tenancy.middleware import TenantMiddleware

_req = ScopeEnforcementMiddleware._required_scope


def test_every_registered_scope_is_a_known_scope() -> None:
    assert set(ENDPOINT_SCOPES.values()) <= _ALL_SCOPES


@pytest.mark.parametrize(
    ("method", "path", "scope"),
    [
        ("GET", "/models", "tenancy:read"),
        ("GET", "/models/active", "tenancy:read"),
        ("GET", "/models/configured", "tenancy:read"),
        ("POST", "/models/test", "tenancy:write"),
        ("POST", "/models/configured", "tenancy:write"),
        ("POST", "/models/configured/reseed", "tenancy:write"),
        ("DELETE", "/models/configured/openai/gpt-4o", "tenancy:write"),
        ("PUT", "/models/routing-policies/coding", "tenancy:write"),
        ("GET", "/api/v1/workflows", "goals:read"),
        ("GET", "/api/v1/workflows/wf-1/versions", "goals:read"),
        ("POST", "/api/v1/workflows", "goals:write"),
        ("POST", "/api/v1/workflows/wf-1/run", "goals:write"),
        ("PATCH", "/api/v1/workflows/wf-1", "goals:write"),
        ("DELETE", "/api/v1/workflows/wf-1", "goals:delete"),
        ("GET", "/api/v1/runs", "goals:read"),
        ("POST", "/api/v1/runs/r-1/cancel", "goals:write"),
        ("GET", "/api/v1/workflow-templates", "goals:read"),
        ("POST", "/api/v1/workflow-templates/x/fork", "goals:write"),
        ("GET", "/api/v1/approvals", "governance:read"),
    ],
)
def test_models_and_workflow_engine_routes_have_scopes(
    method: str, path: str, scope: str
) -> None:
    assert _req(method, path) == scope


def test_every_mounted_models_and_workflow_engine_route_is_scoped() -> None:
    """Enumerate the routers as mounted so a new route can't silently be unscoped."""
    from app.api.model_registry import router as models_router
    from app.workflow.router import router as wf_router
    from app.workflow.router_runs import router as runs_router
    from app.workflow.router_templates import router as tpl_router
    from app.workflow.router_versions import router as ver_router

    app = FastAPI()
    app.include_router(models_router)
    for r in (wf_router, runs_router, tpl_router, ver_router):
        app.include_router(r, prefix="/api/v1")
    missing = [
        (m, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for m in route.methods
        if m not in {"HEAD", "OPTIONS"} and _req(m, route.path) is None
    ]
    assert missing == []


# ── (a) unknown scopes are rejected at key creation ─────────────────────────


async def _client() -> tuple[TestClient, dict[str, str], str]:
    svc = TenantService()
    t = await svc.create_tenant(name="Scopes Corp", email="scopes@example.com")
    await svc.update_plan(t["tenant_id"], "enterprise")
    app = FastAPI()
    app.state.tenant_service = svc
    app.add_middleware(TenantMiddleware, key_resolver=svc.resolve_api_key)
    app.include_router(tenants_router)
    return (
        TestClient(app, raise_server_exceptions=False),
        {"X-API-Key": t["api_key"]},
        t["api_key_id"],
    )


@pytest.mark.parametrize(
    "scopes",
    [["connectors:read"], ["analytics:read"], ["goals:cancel"], ["mcp:*"], ["*"], ["goals:read", "nope"]],
)
async def test_create_key_rejects_unknown_scopes(scopes: list[str]) -> None:
    client, h, _ = await _client()
    resp = client.post("/tenants/me/keys", json={"name": "k", "scopes": scopes}, headers=h)
    assert resp.status_code == 422, resp.text


async def test_create_key_accepts_known_scopes() -> None:
    client, h, _ = await _client()
    resp = client.post(
        "/tenants/me/keys", json={"name": "k", "scopes": ["mcp:read", "audit:read"]}, headers=h
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["scopes"] == ["mcp:read", "audit:read"]


async def test_rotate_key_rejects_unknown_scopes() -> None:
    client, h, key_id = await _client()
    resp = client.post(
        f"/tenants/me/keys/{key_id}/rotate",
        json={"scopes": ["connectors:write"], "revoke_old": False},
        headers=h,
    )
    assert resp.status_code == 422, resp.text
