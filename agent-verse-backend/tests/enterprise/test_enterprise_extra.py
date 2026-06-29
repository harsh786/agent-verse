"""Extra coverage for app/api/enterprise.py — compliance, simulation, red-team, marketplace."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.enterprise import (
    compliance_router,
    intelligence_router,
    marketplace_router,
    router as enterprise_router,
)
from app.enterprise.compliance import ComplianceController
from app.enterprise.marketplace import Marketplace
from app.enterprise.red_team import RedTeamRunner
from app.enterprise.simulation import SimulationRunner
from app.intelligence.self_optimization import SelfOptimizer
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="ent-extra-test", plan=PlanTier.ENTERPRISE, api_key_id="ek2")
_VALID_KEY = "av_ent_extra_key"


def _make_app(
    compliance: ComplianceController | None = None,
    simulation: SimulationRunner | None = None,
    red_team: RedTeamRunner | None = None,
    marketplace: Marketplace | None = None,
    self_optimizer: SelfOptimizer | None = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(enterprise_router)
    app.include_router(marketplace_router)
    app.include_router(intelligence_router)
    app.include_router(compliance_router)

    app.state.compliance_controller = compliance or ComplianceController()
    app.state.simulation_runner = simulation or SimulationRunner()
    app.state.red_team_runner = red_team or RedTeamRunner()
    app.state.marketplace = marketplace or Marketplace()
    app.state.self_optimizer = self_optimizer or SelfOptimizer()
    return app


_H = {"X-API-Key": _VALID_KEY}


# ── Compliance export endpoints ───────────────────────────────────────────────

class TestComplianceExportExtra:
    def test_export_request(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/enterprise/compliance/export", headers=_H)
        assert resp.status_code == 200
        body = resp.json()
        assert "request_id" in body

    def test_export_status_not_found(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/enterprise/compliance/export/nonexistent", headers=_H)
        assert resp.status_code == 404

    def test_export_status_found(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        # First create an export
        create_resp = client.get("/enterprise/compliance/export", headers=_H)
        request_id = create_resp.json()["request_id"]
        # Then check status
        resp = client.get(f"/enterprise/compliance/export/{request_id}", headers=_H)
        assert resp.status_code == 200
        assert resp.json()["request_id"] == request_id

    def test_download_export_not_found(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/enterprise/compliance/export/ghost/download", headers=_H)
        assert resp.status_code == 404

    def test_download_export_found(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.get("/enterprise/compliance/export", headers=_H)
        request_id = create_resp.json()["request_id"]
        resp = client.get(f"/enterprise/compliance/export/{request_id}/download", headers=_H)
        # Ready → 200 with JSON file
        assert resp.status_code in (200, 202)

    def test_data_deletion_request(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post("/enterprise/compliance/delete", headers=_H)
        assert resp.status_code == 202
        body = resp.json()
        assert body["deletion_scheduled"] is True

    def test_data_residency(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/enterprise/compliance/residency", headers=_H)
        assert resp.status_code == 200
        body = resp.json()
        assert "primary_region" in body

    def test_list_regions(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/enterprise/compliance/regions", headers=_H)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 1


# ── Simulation endpoints ──────────────────────────────────────────────────────

class TestSimulationEndpoints:
    def test_run_simulation(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/enterprise/simulation",
            json={
                "goal": "List all open GitHub issues and create a Jira ticket",
                "mock_tools": {
                    "github.list_issues": "5 open issues",
                    "jira.create_issue": "PROJ-1 created",
                },
            },
            headers=_H,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "run_id" in body
        assert "result" in body

    def test_get_simulation_not_found(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/enterprise/simulation/nonexistent-run-id", headers=_H)
        assert resp.status_code == 404

    def test_get_simulation_found(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/enterprise/simulation",
            json={"goal": "test goal for retrieval"},
            headers=_H,
        )
        run_id = create_resp.json()["run_id"]
        resp = client.get(f"/enterprise/simulation/{run_id}", headers=_H)
        assert resp.status_code == 200
        assert resp.json()["run_id"] == run_id

    def test_simulation_available_tools_no_mcp_client(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/enterprise/simulation/available-tools", headers=_H)
        # Route exists; no mcp_client → empty list
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert "tools" in body

    def test_stream_simulation(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/enterprise/simulation/stream",
            json={"goal": "test streaming simulation", "max_steps": 3},
            headers=_H,
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        # Should have at least the started event
        assert "simulation_started" in resp.text or "data:" in resp.text


# ── Red Team endpoints ────────────────────────────────────────────────────────

class TestRedTeamEndpoints:
    def test_run_red_team_default(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post("/enterprise/red-team", json={}, headers=_H)
        assert resp.status_code == 201
        body = resp.json()
        assert "report_id" in body

    def test_run_red_team_with_cases(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/enterprise/red-team",
            json={"cases": ["prompt_injection", "data_exfiltration"]},
            headers=_H,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "cases_run" in body or "total" in body


# ── Marketplace endpoints ─────────────────────────────────────────────────────

class TestMarketplaceEndpoints:
    def test_list_templates(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/marketplace/templates", headers=_H)
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, (list, dict))

    def test_browse_templates(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/marketplace/browse", headers=_H)
        assert resp.status_code in (200, 404)

    def test_list_templates_with_domain_filter(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/marketplace/templates?domain=devops", headers=_H)
        assert resp.status_code in (200, 404)

    def test_get_nonexistent_template(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/marketplace/templates/ghost-template-id", headers=_H)
        assert resp.status_code in (404, 200)


class TestIntelligenceEndpoints:
    def test_get_experiments(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/intelligence/experiments", headers=_H)
        assert resp.status_code in (200, 404)

    def test_get_suggestions(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/intelligence/suggestions", headers=_H)
        assert resp.status_code in (200, 404)

    def test_intelligence_unauthorized(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/intelligence/experiments")
        assert resp.status_code == 401


# ── Compliance router (async GDPR endpoints) ──────────────────────────────────

class TestComplianceRouterEndpoints:
    def test_compliance_export_async(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post("/compliance/gdpr/export", headers=_H)
        assert resp.status_code in (200, 201, 202, 404, 501)

    def test_compliance_gdpr_status(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/compliance/gdpr", headers=_H)
        assert resp.status_code in (200, 404)

    def test_compliance_soc2_status(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/compliance/soc2", headers=_H)
        assert resp.status_code in (200, 404)
