"""QA-6: who is a platform admin — shared by /admin (and the model registry rule).

Regression: /admin only accepted ``X-Admin-Key`` and answered 401 otherwise.
The frontend sends no admin header, and the shared API client logs the user
out on any non-MFA 401, so opening /admin logged an admin out. Now:

* a tenant ``admin`` of an operator tenant is a platform admin
  (``PLATFORM_ADMIN_TENANT_IDS``; ``*`` = any; unset = allowed only outside
  production), or the caller presents the matching ``X-Admin-Key``;
* a missing / wrong key is 403 (never 401), a key presented while
  ``PLATFORM_ADMIN_KEY`` is unset is 503.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.platform_admin import platform_admin_access

_KEY = "test-platform-admin-value"


def _request(roles: tuple[str, ...] | None, tenant_id: str = "t-op", key: str = "") -> Any:
    state = SimpleNamespace()
    if roles is not None:
        state.tenant = TenantContext(
            tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k", roles=roles
        )
    headers = {"x-admin-key": key} if key else {}
    return SimpleNamespace(state=state, headers=headers)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLATFORM_ADMIN_TENANT_IDS", raising=False)
    monkeypatch.delenv("PLATFORM_ADMIN_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")


def test_tenant_admin_allowed_outside_production_when_list_unset() -> None:
    assert platform_admin_access(_request(("admin",))) == (True, "tenant_admin", "", 200)


def test_tenant_admin_refused_in_production_when_list_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    allowed, via, reason, status = platform_admin_access(_request(("admin",)))
    assert (allowed, via, status) == (False, None, 403)
    assert "PLATFORM_ADMIN_TENANT_IDS" in reason


def test_tenant_list_controls_which_admins_qualify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_ADMIN_TENANT_IDS", "t-op, t-other")
    assert platform_admin_access(_request(("admin",), "t-op"))[0] is True
    assert platform_admin_access(_request(("admin",), "t-customer"))[3] == 403
    monkeypatch.setenv("PLATFORM_ADMIN_TENANT_IDS", "*")
    assert platform_admin_access(_request(("admin",), "t-customer"))[0] is True


@pytest.mark.parametrize("roles", [("operator",), ("viewer",), ()])
def test_non_admin_tenant_roles_are_403(roles: tuple[str, ...]) -> None:
    assert platform_admin_access(_request(roles))[3] == 403


def test_admin_key_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Key presented but none configured → 503 (feature not enabled).
    assert platform_admin_access(_request(None, key=_KEY))[3] == 503
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", _KEY)
    assert platform_admin_access(_request(None, key=_KEY)) == (True, "admin_key", "", 200)
    allowed, _, reason, status = platform_admin_access(_request(None, key="wrong"))
    assert (allowed, status) == (False, 403)
    assert "incorrect" in reason
    # No key, no tenant admin → 403, never 401.
    assert platform_admin_access(_request(None))[3] == 403
    # An operator presenting the right key is allowed through the key.
    assert platform_admin_access(_request(("operator",), key=_KEY))[1] == "admin_key"


def test_key_comparison_is_constant_time() -> None:
    import inspect

    from app.tenancy import platform_admin

    assert "hmac.compare_digest" in inspect.getsource(platform_admin.platform_admin_access)


# ── /admin routes use it ─────────────────────────────────────────────────────


def _admin_app(ctx: TenantContext | None) -> TestClient:
    from app.api.admin import router

    app = FastAPI()

    @app.middleware("http")
    async def _auth(request: Any, call_next: Any) -> Any:
        if ctx is not None:
            request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    app.state.tenant_service = SimpleNamespace(_tenants={})
    return TestClient(app, raise_server_exceptions=False)


def test_admin_route_accepts_operator_tenant_admin_without_key() -> None:
    ctx = TenantContext(tenant_id="t-op", plan=PlanTier.FREE, api_key_id="k", roles=("admin",))
    resp = _admin_app(ctx).get("/admin/tenants")
    assert resp.status_code == 200, resp.text


def test_admin_route_refuses_non_admin_with_403_not_401() -> None:
    ctx = TenantContext(tenant_id="t-op", plan=PlanTier.FREE, api_key_id="k", roles=("operator",))
    resp = _admin_app(ctx).get("/admin/tenants")
    assert resp.status_code == 403, resp.text
