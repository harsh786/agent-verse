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

    # QA-6: refusals are 403, never 401 — the web client logs out on a 401.
    def test_wrong_key_returns_403(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/tenants", headers={"X-Admin-Key": "wrong"})
        assert resp.status_code == 403

    def test_missing_header_returns_403(self):
        client = TestClient(_make_app())
        resp = client.get("/admin/tenants")
        assert resp.status_code == 403


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

    def test_cost_controller_error_is_reported_not_an_empty_usage(self):
        # a10-F239-02: a failed read used to be swallowed into usage == {},
        # indistinguishable from "no spend today".
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
        assert resp.json()["usage"] is None
        assert resp.json()["usage_error"]

    def test_usage_reads_the_cost_tracker_not_the_plain_controller(self):
        """a10-F239-02: the plain in-memory CostController has no get_budget_status,
        so usage was always {}. The cost tracker (what /costs shows) is read."""
        from app.governance.cost import CostController

        app = _make_app()
        app.state.tenant_service = types.SimpleNamespace(
            _tenants={"t1": {"tenant_id": "t1", "plan": "free"}}
        )
        app.state.cost_controller = CostController()
        tracker = AsyncMock()
        tracker.get_budget_status.return_value = {"daily_spent": 1.5, "daily_limit": 10.0}
        app.state.cost_tracker = tracker
        resp = TestClient(app).get("/admin/tenants/t1", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["usage"] == {"daily_spent": 1.5, "daily_limit": 10.0}
        tracker.get_budget_status.assert_awaited_once_with("t1")

    def test_no_budget_source_is_reported(self):
        from app.governance.cost import CostController

        app = _make_app()
        app.state.tenant_service = types.SimpleNamespace(
            _tenants={"t1": {"tenant_id": "t1", "plan": "free"}}
        )
        app.state.cost_controller = CostController()
        resp = TestClient(app).get("/admin/tenants/t1", headers=_ADMIN_HEADERS)
        assert resp.json()["usage"] is None and resp.json()["usage_error"]

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

    def test_plan_change_goes_through_the_durable_update_plan(self):
        """Regression: the endpoint edited only this replica's in-memory dict."""
        from app.services.tenant_service import TenantService

        app = _make_app()
        svc = TenantService()
        import asyncio

        t = asyncio.run(svc.create_tenant(name="Acme", email="plan@acme.test"))
        app.state.tenant_service = svc
        resp = TestClient(app).put(
            f"/admin/tenants/{t['tenant_id']}/plan",
            headers=_ADMIN_HEADERS,
            json={"plan": "enterprise"},
        )
        assert resp.status_code == 200
        assert asyncio.run(svc.get_tenant(t["tenant_id"]))["plan"] == "enterprise"

    def test_unknown_tenant_is_404(self):
        from app.services.tenant_service import TenantService

        app = _make_app()
        app.state.tenant_service = TenantService()
        resp = TestClient(app).put(
            "/admin/tenants/nope/plan", headers=_ADMIN_HEADERS, json={"plan": "starter"}
        )
        assert resp.status_code == 404

    def test_no_tenant_service_is_503_not_a_fake_update(self):
        resp = TestClient(_make_app()).put(
            "/admin/tenants/t1/plan", headers=_ADMIN_HEADERS, json={"plan": "starter"}
        )
        assert resp.status_code == 503


class _SysSession:
    """Fake maintenance session: routes a Core select by one of its labels."""

    def __init__(self, routes: dict[str, list[dict]], fail: bool = False) -> None:
        self.routes = routes
        self.fail = fail
        self.sql: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _b():
            yield self

        return _b()

    async def execute(self, stmt, params=None):
        q = str(stmt)
        self.sql.append(q)
        res = types.SimpleNamespace()
        if "row_security" in q:
            return res
        if self.fail:
            raise RuntimeError("connection refused")
        labels = set(getattr(stmt, "selected_columns", {}).keys())
        for marker, rows in self.routes.items():
            if marker in labels:
                res.mappings = lambda rows=rows: types.SimpleNamespace(
                    all=lambda: rows, one=lambda: rows[0]
                )
                return res
        raise AssertionError(f"unrouted statement: {labels}")


class TestPlatformUsage:
    """Regression: /admin/usage read GoalService._active_goals, which does not
    exist, so active_goals was always 0; tenant count came from one replica's
    in-memory cache. It now counts the goals and tenants tables."""

    def _usage_session(self, **kw):
        return _SysSession(
            {
                "active": [
                    {
                        "total": 40, "active": 3, "goals_today": 7,
                        "completed_today": 4, "avg_latency_s_today": 12.5,
                    }
                ],
                "status": [
                    {"status": "complete", "n": 30},
                    {"status": "executing", "n": 3},
                    {"status": "failed", "n": 7},
                ],
                "total_tenants": [{"total_tenants": 5}],
            },
            **kw,
        )

    def test_counts_goals_and_tenants_from_the_database(self):
        app = _make_app()
        session = self._usage_session()
        app.state.system_db_session_factory = lambda: session
        # Stale in-memory state must not leak into the answer.
        app.state.goal_service = types.SimpleNamespace(_active_goals={"g1": 1})
        app.state.tenant_service = types.SimpleNamespace(_tenants={"t1": {}})
        resp = TestClient(app).get("/admin/usage", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["active_goals"] == 3
        assert data["total_tenants"] == 5
        assert data["total_goals"] == 40
        assert data["goals_today"] == 7
        assert data["avg_latency_ms"] == 12500
        assert data["goals_by_status"] == {"complete": 30, "executing": 3, "failed": 7}
        assert any("row_security = off" in q for q in session.sql)

    def test_no_completed_goal_today_has_null_latency(self):
        app = _make_app()
        session = _SysSession(
            {
                "active": [
                    {"total": 0, "active": 0, "goals_today": 0, "completed_today": 0,
                     "avg_latency_s_today": None}
                ],
                "status": [],
                "total_tenants": [{"total_tenants": 0}],
            }
        )
        app.state.system_db_session_factory = lambda: session
        data = TestClient(app).get("/admin/usage", headers=_ADMIN_HEADERS).json()
        assert data["avg_latency_ms"] is None
        assert data["active_goals"] == 0

    def test_no_database_is_501_not_zeros(self):
        resp = TestClient(_make_app()).get("/admin/usage", headers=_ADMIN_HEADERS)
        assert resp.status_code == 501

    def test_database_error_is_503_not_zeros(self):
        app = _make_app()
        session = self._usage_session(fail=True)
        app.state.system_db_session_factory = lambda: session
        resp = TestClient(app).get("/admin/usage", headers=_ADMIN_HEADERS)
        assert resp.status_code == 503

    def test_repeated_polls_reuse_one_computation(self, monkeypatch):
        """a10-F239-05: every poll ran the whole-table aggregates again."""
        import app.api.admin as admin_mod

        app = _make_app()
        opened: list[object] = []

        def _factory():
            session = self._usage_session()
            opened.append(session)
            return session

        app.state.system_db_session_factory = _factory
        client = TestClient(app)
        first = client.get("/admin/usage", headers=_ADMIN_HEADERS).json()
        second = client.get("/admin/usage", headers=_ADMIN_HEADERS).json()
        assert len(opened) == 1
        assert first == second and first["as_of"]
        # Past the TTL the aggregates run again.
        monkeypatch.setattr(admin_mod, "USAGE_CACHE_TTL_S", 0.0)
        client.get("/admin/usage", headers=_ADMIN_HEADERS)
        assert len(opened) == 2

    def test_errors_are_not_cached(self):
        app = _make_app()
        sessions = [self._usage_session(fail=True), self._usage_session()]
        app.state.system_db_session_factory = lambda: sessions.pop(0)
        client = TestClient(app)
        assert client.get("/admin/usage", headers=_ADMIN_HEADERS).status_code == 503
        assert client.get("/admin/usage", headers=_ADMIN_HEADERS).status_code == 200


class TestIncidents:
    """Regression: /admin/incidents read guardrail_engine._incidents, which no
    engine defines, so the feed was always empty. Nothing persists guardrail
    incidents, so the endpoint is an honest 501."""

    def test_is_501_with_a_reason(self):
        app = _make_app()
        app.state.guardrail_engine = types.SimpleNamespace(_incidents=[{"id": 1}])
        resp = TestClient(app).get("/admin/incidents", headers=_ADMIN_HEADERS)
        assert resp.status_code == 501
        assert "not persisted" in resp.json()["detail"]

    def test_still_requires_the_admin_key(self):
        resp = TestClient(_make_app()).get("/admin/incidents")
        assert resp.status_code == 403


def test_tenant_context_replace_smoke():
    # sanity-check the module's own PlanTier/replace usage matches expectations
    ctx = TenantContext(tenant_id="t", plan=PlanTier.FREE, api_key_id="k")
    updated = replace(ctx, plan=PlanTier.ENTERPRISE)
    assert updated.plan == PlanTier.ENTERPRISE


def test_module_docstring_lists_only_mounted_routes() -> None:
    """a10-F239-04: the docstring advertised POST .../keys/revoke, which never existed."""
    import re

    import app.api.admin as admin_mod

    mounted = {(m, r.path) for r in router.routes for m in getattr(r, "methods", ())}
    listed = re.findall(
        r"^\s+(GET|POST|PUT|DELETE|PATCH)\s+(/admin/\S+)", admin_mod.__doc__ or "", re.M
    )
    assert listed
    for method, path in listed:
        head, _, tail = path.rpartition("/")
        for leaf in tail.split("|"):
            variant = f"{head}/{leaf}".replace("{id}", "{mapping_id}")
            assert (method, variant) in mounted, (method, variant)
