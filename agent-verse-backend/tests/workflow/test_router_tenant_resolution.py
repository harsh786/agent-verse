"""Tenant-resolution unit tests for the workflow sub-routers.

The HITL, template, and version sub-routers each resolve the current tenant via
a ``_tenant_id(request)`` helper. In production the tenant is set per-request by
``TenantMiddleware`` on ``request.state.tenant``; ``request.app.state`` never has
a ``tenant_context`` attribute. These tests pin the resolution contract:

  1. Prefer the per-request ``request.state.tenant``.
  2. Fall back to ``request.app.state.tenant_context`` when no per-request tenant.
  3. Raise ``HTTPException(401)`` when neither is present.

Requests are faked with ``SimpleNamespace`` so the helper is exercised in
isolation from the full FastAPI/middleware stack.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.workflow.router import _get_tenant
from app.workflow.router_hitl import _tenant_id as hitl_tenant_id
from app.workflow.router_runs import _tenant_id as runs_tenant_id
from app.workflow.router_templates import _tenant_id as templates_tenant_id
from app.workflow.router_versions import _tenant_id as versions_tenant_id

# These helpers each return the tenant *id* string; they share one contract.
_RESOLVERS = pytest.mark.parametrize(
    "resolve",
    [hitl_tenant_id, templates_tenant_id, versions_tenant_id, runs_tenant_id],
    ids=["hitl", "templates", "versions", "runs"],
)


def _make_request(*, request_tenant: object | None, app_tenant: object | None) -> SimpleNamespace:
    """Build a fake request whose ``.state``/``.app.state`` mimic Starlette's."""
    state = SimpleNamespace()
    if request_tenant is not None:
        state.tenant = request_tenant
    app_state = SimpleNamespace()
    if app_tenant is not None:
        app_state.tenant_context = app_tenant
    return SimpleNamespace(state=state, app=SimpleNamespace(state=app_state))


@_RESOLVERS
def test_prefers_per_request_tenant(resolve) -> None:
    request = _make_request(
        request_tenant=SimpleNamespace(tenant_id="req-tenant"),
        app_tenant=SimpleNamespace(tenant_id="app-tenant"),
    )
    assert resolve(request) == "req-tenant"


@_RESOLVERS
def test_falls_back_to_app_state(resolve) -> None:
    request = _make_request(
        request_tenant=None,
        app_tenant=SimpleNamespace(tenant_id="app-tenant"),
    )
    assert resolve(request) == "app-tenant"


@_RESOLVERS
def test_raises_401_when_no_tenant(resolve) -> None:
    request = _make_request(request_tenant=None, app_tenant=None)
    with pytest.raises(HTTPException) as exc_info:
        resolve(request)
    assert exc_info.value.status_code == 401


# ── router.py::_get_tenant returns the tenant *object*, same contract ──────────


def test_get_tenant_prefers_per_request() -> None:
    request = _make_request(
        request_tenant=SimpleNamespace(tenant_id="req-tenant"),
        app_tenant=SimpleNamespace(tenant_id="app-tenant"),
    )
    assert _get_tenant(request).tenant_id == "req-tenant"


def test_get_tenant_falls_back_to_app_state() -> None:
    request = _make_request(
        request_tenant=None,
        app_tenant=SimpleNamespace(tenant_id="app-tenant"),
    )
    assert _get_tenant(request).tenant_id == "app-tenant"


def test_get_tenant_raises_401_when_no_tenant() -> None:
    request = _make_request(request_tenant=None, app_tenant=None)
    with pytest.raises(HTTPException) as exc_info:
        _get_tenant(request)
    assert exc_info.value.status_code == 401
