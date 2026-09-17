"""Tests for the platform admin API (app/api/admin.py)."""
from __future__ import annotations

import types
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.tenancy.context import PlanTier, TenantContext

_ADMIN_KEY = "platform-admin-secret"
_ADMIN_HEADERS = {"X-Admin-Key": _ADMIN_KEY}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _admin_key_env(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", _ADMIN_KEY)


class TestRequireAdmin:
    def test_missing_env_returns_503(self, monkeypatch):
        monkeypatch.delenv("PLATFORM_ADMIN_KEY", raising=False)
        client = TestClient(_make_app())
        resp = client.get("/admin/tenants", headers=_ADMIN_HEADERS)
        assert resp.status_code == 503

    def test_wrong_key_returns_401(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/tenants", headers={"X-Admin-Key": "wrong"})
        assert resp.status_code == 401

    def test_missing_header_returns_401(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/tenants")
        assert resp.status_code == 401


class TestListTenants:
    def test_no_tenant_service_returns_503(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/tenants", headers=_ADMIN_HEADERS)
        assert resp.status_code == 503

    def test_lists_dict_tenants_with_pagination(self):
        app = _make_app()
        tenants = {
            f"t{i}": {"tenant_id": f"t{i}", "plan": "free", "name": f"Tenant {i}", "email": f"t{i}@x.com"}
            for i in range(5)
        }
        app.state.tenant_service = types.SimpleNamespace(_tenants=tenants)
        client = TestClient(app)
        resp = client.get("/admin/tenants", headers=_ADMIN_HEADERS, params={"limit": 2, "offset": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert data["limit"] == 2
        assert data["offset"] == 1
        assert len(data["tenants"]) == 2

    def test_search_filters_by_name_email_or_id(self):
        app = _make_app()
        tenants = {
            "t1": {"tenant_id": "t1", "plan": "free", "name": "Acme Corp", "email": "a@acme.com"},
            "t2": {"tenant_id": "t2", "plan": "free", "name": "Other", "email": "b@other.com"},
        }
        app.state.tenant_service = types.SimpleNamespace(_tenants=tenants)
        client = TestClient(app)
        resp = client.get("/admin/tenants", headers=_ADMIN_HEADERS, params={"search": "acme"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["tenants"][0]["tenant_id"] == "t1"

    def test_lists_tenant_context_objects(self):
        app = _make_app()
        ctx = TenantContext(tenant_id="tc1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
        app.state.tenant_service = types.SimpleNamespace(_tenants={"tc1": ctx})
        client = TestClient(app)
        resp = client.get("/admin/tenants", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenants"] == [{"tenant_id": "tc1", "plan": "enterprise"}]

    def test_exception_returns_500(self):
        app = _make_app()

        class _BadTenantSvc:
            @property
            def _tenants(self):
                raise RuntimeError("boom")

        app.state.tenant_service = _BadTenantSvc()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/admin/tenants", headers=_ADMIN_HEADERS)
        assert resp.status_code == 500


class TestGetTenantDetail:
    def test_tenant_not_found_returns_404(self):
        app = _make_app()
        app.state.tenant_service = types.SimpleNamespace(_tenants={})
        client = TestClient(app)
        resp = client.get("/admin/tenants/nope", headers=_ADMIN_HEADERS)
        assert resp.status_code == 404

    def test_dict_tenant_with_usage(self):
        app = _make_app()
        app.state.tenant_service = types.SimpleNamespace(
            _tenants={"t1": {"tenant_id": "t1", "plan": "starter"}}
        )
        cost_ctrl = AsyncMock()
        cost_ctrl.get_budget_status.return_value = {"spent": 10}
        app.state.cost_controller = cost_ctrl
        client = TestClient(app)
        resp = client.get("/admin/tenants/t1", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "starter"
        assert data["usage"] == {"spent": 10}

    def test_tenant_context_object_plan(self):
        app = _make_app()
        ctx = TenantContext(tenant_id="t2", plan=PlanTier.PROFESSIONAL, api_key_id="k2")
        app.state.tenant_service = types.SimpleNamespace(_tenants={"t2": ctx})
        client = TestClient(app)
        resp = client.get("/admin/tenants/t2", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["plan"] == "professional"

    def test_cost_controller_error_yields_empty_usage(self):
        app = _make_app()
        app.state.tenant_service = types.SimpleNamespace(
            _tenants={"t1": {"tenant_id": "t1", "plan": "free"}}
        )
        cost_ctrl = AsyncMock()
        cost_ctrl.get_budget_status.side_effect = RuntimeError("down")
        app.state.cost_controller = cost_ctrl
        client = TestClient(app)
        resp = client.get("/admin/tenants/t1", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["usage"] == {}

    def test_no_tenant_service_returns_404(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/tenants/t1", headers=_ADMIN_HEADERS)
        assert resp.status_code == 404


class TestChangeTenantPlan:
    def test_invalid_plan_returns_400(self):
        app = _make_app()
        app.state.tenant_service = types.SimpleNamespace(_tenants={})
        client = TestClient(app)
        resp = client.put(
            "/admin/tenants/t1/plan", headers=_ADMIN_HEADERS, json={"plan": "not-a-plan"}
        )
        assert resp.status_code == 400

    def test_updates_dict_tenant_in_place(self):
        app = _make_app()
        tenants = {"t1": {"tenant_id": "t1", "plan": "free"}}
        app.state.tenant_service = types.SimpleNamespace(_tenants=tenants)
        client = TestClient(app)
        resp = client.put(
            "/admin/tenants/t1/plan", headers=_ADMIN_HEADERS, json={"plan": "enterprise"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"tenant_id": "t1", "plan": "enterprise", "status": "updated"}
        assert tenants["t1"]["plan"] == "enterprise"

    def test_replaces_tenant_context_dataclass(self):
        app = _make_app()
        ctx = TenantContext(tenant_id="t2", plan=PlanTier.FREE, api_key_id="k2")
        tenants = {"t2": ctx}
        app.state.tenant_service = types.SimpleNamespace(_tenants=tenants)
        client = TestClient(app)
        resp = client.put(
            "/admin/tenants/t2/plan", headers=_ADMIN_HEADERS, json={"plan": "starter"}
        )
        assert resp.status_code == 200
        assert tenants["t2"].plan == PlanTier.STARTER

    def test_unknown_tenant_id_is_noop_but_200(self):
        app = _make_app()
        app.state.tenant_service = types.SimpleNamespace(_tenants={})
        client = TestClient(app)
        resp = client.put(
            "/admin/tenants/nope/plan", headers=_ADMIN_HEADERS, json={"plan": "starter"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_no_tenant_service_still_200(self):
        client = TestClient(_make_app())
        resp = client.put(
            "/admin/tenants/t1/plan", headers=_ADMIN_HEADERS, json={"plan": "starter"}
        )
        assert resp.status_code == 200


class TestPlatformUsage:
    def test_aggregates_active_goals_and_tenants(self):
        app = _make_app()
        app.state.goal_service = types.SimpleNamespace(_active_goals={"g1": 1, "g2": 2})
        app.state.tenant_service = types.SimpleNamespace(_tenants={"t1": {}, "t2": {}})
        client = TestClient(app)
        resp = client.get("/admin/usage", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {"active_goals": 2, "total_tenants": 2}

    def test_missing_services_defaults_to_zero(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/usage", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {"active_goals": 0, "total_tenants": 0}

    def test_goal_service_error_is_suppressed(self):
        app = _make_app()

        class _BadGoalSvc:
            @property
            def _active_goals(self):
                raise RuntimeError("boom")

        app.state.goal_service = _BadGoalSvc()
        client = TestClient(app)
        resp = client.get("/admin/usage", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["active_goals"] == 0


class TestIncidents:
    def test_no_guardrail_engine_returns_empty(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/incidents", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {"incidents": [], "total": 0}

    def test_returns_recent_incidents_limited(self):
        app = _make_app()
        incidents = [{"id": i} for i in range(10)]
        app.state.guardrail_engine = types.SimpleNamespace(_incidents=incidents)
        client = TestClient(app)
        resp = client.get("/admin/incidents", headers=_ADMIN_HEADERS, params={"limit": 3})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["incidents"] == incidents[-3:]

    def test_guardrail_engine_error_returns_empty(self):
        app = _make_app()

        class _BadEngine:
            @property
            def _incidents(self):
                raise RuntimeError("boom")

        app.state.guardrail_engine = _BadEngine()
        client = TestClient(app)
        resp = client.get("/admin/incidents", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {"incidents": [], "total": 0}


def test_tenant_context_replace_smoke():
    # sanity-check the module's own PlanTier/replace usage matches expectations
    ctx = TenantContext(tenant_id="t", plan=PlanTier.FREE, api_key_id="k")
    updated = replace(ctx, plan=PlanTier.ENTERPRISE)
    assert updated.plan == PlanTier.ENTERPRISE
