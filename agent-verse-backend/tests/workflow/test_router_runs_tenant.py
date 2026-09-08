"""Run routers must resolve the tenant per-request, not from a global.

``router_runs._tenant_id`` read ``request.app.state.tenant_context`` — an
attribute nothing ever sets — so ``GET /runs/{id}`` 500'd (AttributeError), and
had it been set it would have been a single global shared across all tenants.
The fix mirrors ``app/workflow/router.py::_get_tenant``: prefer the per-request
``request.state.tenant`` (set by TenantMiddleware), fall back to the app-state
context.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.workflow.router_runs import _tenant_id


def _req(*, req_tenant: object, app_tenant_context: object) -> SimpleNamespace:
    app_state = SimpleNamespace()
    if app_tenant_context is not _MISSING:
        app_state.tenant_context = app_tenant_context
    return SimpleNamespace(
        state=SimpleNamespace(tenant=req_tenant),
        app=SimpleNamespace(state=app_state),
    )


_MISSING = object()


def test_prefers_per_request_tenant() -> None:
    req = _req(
        req_tenant=SimpleNamespace(tenant_id="T-req"),
        app_tenant_context=SimpleNamespace(tenant_id="T-app"),
    )
    assert _tenant_id(req) == "T-req"


def test_falls_back_to_app_state_context() -> None:
    req = _req(
        req_tenant=None,
        app_tenant_context=SimpleNamespace(tenant_id="T-app"),
    )
    assert _tenant_id(req) == "T-app"


def test_raises_401_when_no_tenant_anywhere() -> None:
    req = _req(req_tenant=None, app_tenant_context=_MISSING)
    with pytest.raises(HTTPException) as exc:
        _tenant_id(req)
    assert exc.value.status_code == 401
