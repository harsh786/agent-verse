"""API-level tests for enterprise endpoints using TestClient."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.enterprise import (
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

_CTX = TenantContext(tenant_id="api-ent-test", plan=PlanTier.ENTERPRISE, api_key_id="ekey-api")
_VALID_KEY = "av_test_enterprise"


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

    app.state.compliance_controller = compliance or ComplianceController()
    app.state.simulation_runner = simulation or SimulationRunner()
    app.state.red_team_runner = red_team or RedTeamRunner()
    app.state.marketplace = marketplace or Marketplace()
    app.state.self_optimizer = self_optimizer or SelfOptimizer()
    return app


_HDR = {"X-API-Key": _VALID_KEY}

# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_enterprise_endpoints_require_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    assert client.get("/enterprise/compliance/export").status_code == 401
    assert client.get("/enterprise/compliance/residency").status_code == 401
    assert client.post("/enterprise/compliance/delete").status_code == 401
    assert client.post("/enterprise/simulation", json={"goal": "test"}).status_code == 401
    assert client.post("/enterprise/red-team", json={}).status_code == 401
    assert client.get("/marketplace/browse").status_code == 401
    assert client.get("/intelligence/suggestions").status_code == 401


# ---------------------------------------------------------------------------
# Compliance endpoints
# ---------------------------------------------------------------------------

def test_api_compliance_export() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/enterprise/compliance/export", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert "request_id" in body
    assert body["download_url"].startswith("/compliance/export/")


def test_api_compliance_export_status() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # First create an export
    create_resp = client.get("/enterprise/compliance/export", headers=_HDR)
    request_id = create_resp.json()["request_id"]

    # Then fetch its status
    status_resp = client.get(
        f"/enterprise/compliance/export/{request_id}", headers=_HDR
    )
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["request_id"] == request_id
    assert "payload" in body


def test_api_compliance_export_status_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/enterprise/compliance/export/ghost-id", headers=_HDR)
    assert resp.status_code == 404


def test_api_compliance_deletion() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/enterprise/compliance/delete", headers=_HDR)
    assert resp.status_code == 202
    body = resp.json()
    assert body["deletion_scheduled"] is True
    assert body["tenant_id"] == _CTX.tenant_id


def test_api_compliance_residency() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/enterprise/compliance/residency", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["gdpr_compliant"] is True
    assert body["tenant_id"] == _CTX.tenant_id


# ---------------------------------------------------------------------------
# Simulation endpoints
# ---------------------------------------------------------------------------

def test_api_simulation_run() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/enterprise/simulation",
        json={"goal": "Test the payment flow", "mock_tools": {"stripe": {"status": "ok"}}},
        headers=_HDR,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"
    assert "run_id" in body
    assert "result" in body


def test_api_simulation_get() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    create_resp = client.post(
        "/enterprise/simulation",
        json={"goal": "Check logs"},
        headers=_HDR,
    )
    run_id = create_resp.json()["run_id"]

    get_resp = client.get(f"/enterprise/simulation/{run_id}", headers=_HDR)
    assert get_resp.status_code == 200
    assert get_resp.json()["run_id"] == run_id


def test_api_simulation_get_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/enterprise/simulation/no-such-run", headers=_HDR)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Red-team endpoints
# ---------------------------------------------------------------------------

def test_api_red_team_run_all() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/enterprise/red-team", json={}, headers=_HDR)
    assert resp.status_code == 201
    body = resp.json()
    assert body["cases_run"] == 5
    assert "report_id" in body
    assert isinstance(body["results"], list)
    assert len(body["results"]) == 5


def test_api_red_team_run_subset() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/enterprise/red-team",
        json={"cases": ["prompt_injection"]},
        headers=_HDR,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["cases_run"] == 1
    assert body["results"][0]["case_id"] == "prompt_injection"


# ---------------------------------------------------------------------------
# Marketplace endpoints
# ---------------------------------------------------------------------------

def test_api_marketplace_browse_all() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/marketplace/browse", headers=_HDR)
    assert resp.status_code == 200
    templates = resp.json()
    assert len(templates) >= 6


def test_api_marketplace_browse_by_domain() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/marketplace/browse?domain=devops", headers=_HDR)
    assert resp.status_code == 200
    templates = resp.json()
    assert len(templates) >= 1
    assert all(t["domain"] == "devops" for t in templates)


def test_api_marketplace_browse_by_query() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/marketplace/browse?q=bug", headers=_HDR)
    assert resp.status_code == 200
    templates = resp.json()
    assert len(templates) >= 1


def test_api_marketplace_get_template() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/marketplace/tpl-hr-onboarding", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["template_id"] == "tpl-hr-onboarding"


def test_api_marketplace_get_template_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/marketplace/tpl-does-not-exist", headers=_HDR)
    assert resp.status_code == 404


def test_api_marketplace_deploy_template() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/marketplace/tpl-bug-fix/deploy",
        json={"params": {"repo": "acme/backend", "label": "prod-p1"}},
        headers=_HDR,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["template_id"] == "tpl-bug-fix"
    assert "deployment_id" in body
    assert "agent_id" in body


def test_api_marketplace_deploy_unknown_template() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/marketplace/tpl-ghost/deploy",
        json={"params": {}},
        headers=_HDR,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Intelligence / self-optimization endpoints
# ---------------------------------------------------------------------------

def _seed_suggestions(client: TestClient) -> list[dict[str, Any]]:
    """Helper: seed the optimizer with low-score eval data via the unit layer."""
    from app.intelligence.eval import EvalScorecard
    from app.intelligence.self_optimization import SelfOptimizer

    opt = SelfOptimizer()
    opt.analyze_and_suggest(
        goal="deploy service",
        scorecard=EvalScorecard(
            goal_id="g-seed",
            scores={
                "task_completion": 0.1,
                "accuracy": 0.1,
                "efficiency": 0.1,
                "safety": 0.1,
                "coherence": 0.1,
            },
        ),
        error_log="tool not found",
        tenant_ctx=_CTX,
    )
    return opt.list_suggestions(tenant_ctx=_CTX)


def test_api_suggestions_empty_initially() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/intelligence/suggestions", headers=_HDR)
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_suggestions_apply() -> None:
    """Seeds optimizer directly on app.state, then applies via API."""
    from app.intelligence.eval import EvalScorecard

    opt = SelfOptimizer()
    suggestions = opt.analyze_and_suggest(
        goal="goal",
        scorecard=EvalScorecard(
            goal_id="g-api",
            scores={
                "task_completion": 0.1,
                "accuracy": 0.1,
                "efficiency": 0.1,
                "safety": 0.1,
                "coherence": 0.1,
            },
        ),
        error_log="",
        tenant_ctx=_CTX,
    )
    sid = suggestions[0].suggestion_id

    app = _make_app(self_optimizer=opt)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(f"/intelligence/suggestions/{sid}/apply", headers=_HDR)
    assert resp.status_code == 200
    assert resp.json()["applied"] is True

    # Confirm it appears in the applied filter
    list_resp = client.get("/intelligence/suggestions?applied=true", headers=_HDR)
    assert any(s["suggestion_id"] == sid for s in list_resp.json())


def test_api_suggestions_apply_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/intelligence/suggestions/ghost-sid/apply", headers=_HDR)
    assert resp.status_code == 404


def test_api_suggestions_reject() -> None:
    from app.intelligence.eval import EvalScorecard

    opt = SelfOptimizer()
    suggestions = opt.analyze_and_suggest(
        goal="goal",
        scorecard=EvalScorecard(
            goal_id="g-rej",
            scores={
                "task_completion": 0.1,
                "accuracy": 0.1,
                "efficiency": 0.1,
                "safety": 0.1,
                "coherence": 0.1,
            },
        ),
        error_log="",
        tenant_ctx=_CTX,
    )
    sid = suggestions[0].suggestion_id

    app = _make_app(self_optimizer=opt)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(f"/intelligence/suggestions/{sid}/reject", headers=_HDR)
    assert resp.status_code == 200
    assert resp.json()["rejected"] is True


def test_api_suggestions_reject_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/intelligence/suggestions/ghost-sid/reject", headers=_HDR)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Marketplace publish (W-8)
# ---------------------------------------------------------------------------

def test_api_marketplace_publish() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/marketplace/publish", json={
        "name": "My AI Agent",
        "domain": "finance",
        "description": "Automates invoice reconciliation with high accuracy",
    }, headers=_HDR)
    assert resp.status_code == 201
    data = resp.json()
    assert data["template_id"].startswith("tpl-custom-")
    assert data["published_by"] == _CTX.tenant_id


def test_api_marketplace_publish_appears_in_browse() -> None:
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    client.post("/marketplace/publish", json={
        "name": "Test Community Agent",
        "domain": "testing",
        "description": "A test agent for the community marketplace",
    }, headers=_HDR)
    resp = client.get("/marketplace/browse", headers=_HDR)
    templates = resp.json()
    assert any(t.get("is_community") and "Test Community Agent" in t["name"] for t in templates)
