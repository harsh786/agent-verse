"""Tests for real red-team runner with guardrail detection."""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.enterprise.red_team import RedTeamRunner
from app.tenancy.context import TenantContext, PlanTier


_CTX = TenantContext(tenant_id="rt-test", plan=PlanTier.FREE, api_key_id="k1")


def test_red_team_runner_returns_report():
    """RedTeamRunner produces a valid report with all built-in cases."""
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=_CTX)

    assert report.cases_run == 5
    assert report.cases_passed + report.cases_failed == report.cases_run
    assert len(report.results) == report.cases_run
    for r in report.results:
        assert "case_id" in r
        assert r["status"] in ("passed", "failed", "blocked", "allowed", "error")


def test_red_team_specific_cases():
    """Run a subset of cases by ID."""
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=_CTX, cases=["prompt_injection"])

    assert report.cases_run == 1
    assert len(report.results) == 1
    assert report.results[0]["case_id"] == "prompt_injection"


def test_red_team_report_has_report_id():
    """Each report gets a unique report_id."""
    runner = RedTeamRunner()
    r1 = runner.run(tenant_ctx=_CTX)
    r2 = runner.run(tenant_ctx=_CTX)
    assert r1.report_id != r2.report_id


def test_red_team_report_frontend_aliases():
    """RedTeamReport exposes frontend-friendly property aliases."""
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=_CTX)

    assert report.total == report.cases_run
    assert report.passed == report.cases_passed
    assert report.failed == report.cases_failed
    assert isinstance(report.run_at, str)


def test_red_team_get_report():
    """get_report returns the stored report by ID."""
    runner = RedTeamRunner()
    report = runner.run(tenant_ctx=_CTX)
    fetched = runner.get_report(report_id=report.report_id, tenant_ctx=_CTX)
    assert fetched is not None
    assert fetched.report_id == report.report_id


def test_red_team_get_nonexistent_report_returns_none():
    runner = RedTeamRunner()
    result = runner.get_report(report_id="nonexistent", tenant_ctx=_CTX)
    assert result is None


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def authed_client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/tenants/signup", json={"name": "Test", "email": "t@test.com"})
        assert resp.status_code == 201
        c.headers["X-API-Key"] = resp.json()["api_key"]
        yield c


async def test_red_team_api_endpoint(authed_client):
    resp = await authed_client.post("/enterprise/red-team", json={})
    assert resp.status_code == 201
    data = resp.json()
    # Response must include at least one of these count keys
    assert "cases_run" in data or "total" in data
    assert "results" in data
    assert isinstance(data["results"], list)
    # Default run executes all 5 built-in cases
    assert data.get("cases_run", data.get("total", 0)) == 5


async def test_red_team_api_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/enterprise/red-team", json={})
        assert resp.status_code == 401


async def test_red_team_api_subset(authed_client):
    """Running a subset via the API returns only the requested cases."""
    resp = await authed_client.post(
        "/enterprise/red-team", json={"cases": ["data_exfiltration"]}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data.get("cases_run", data.get("total", -1)) == 1
    assert data["results"][0]["case_id"] == "data_exfiltration"
