"""Phase 12+13: Skills Runtime tests."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.skills_runtime import router as skills_router, _platform_skills, _enabled_skills
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-p1213", plan=PlanTier.PROFESSIONAL, api_key_id="kid-p1213")
_CTX_B = TenantContext(tenant_id="tid-p1213b", plan=PlanTier.FREE, api_key_id="kid-p1213b")
_KEY = "ak_phase1213_test_key"
_KEY_B = "ak_phase1213b_test_key"
_HEADERS = {"X-API-Key": _KEY}
_HEADERS_B = {"X-API-Key": _KEY_B}


def _make_app():
    app = FastAPI()

    async def _resolve(key):
        if key == _KEY:
            return _CTX
        if key == _KEY_B:
            return _CTX_B
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(skills_router)
    return app


def test_list_platform_skills_includes_builtins():
    client = TestClient(_make_app())
    resp = client.get("/skills-runtime", headers=_HEADERS)
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    assert len(skills) >= 8  # All 8 built-in skills
    names = {s["name"] for s in skills}
    assert "Graphify" in names
    assert "Code Review" in names
    assert "Test Writer" in names


def test_get_specific_skill():
    client = TestClient(_make_app())
    resp = client.get("/skills-runtime/graphify", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["skill_id"] == "graphify"
    assert resp.json()["is_builtin"] is True


def test_get_nonexistent_skill_returns_404():
    client = TestClient(_make_app())
    resp = client.get("/skills-runtime/nonexistent-skill-xyz", headers=_HEADERS)
    assert resp.status_code == 404


def test_enable_platform_skill():
    client = TestClient(_make_app())
    resp = client.post("/skills-runtime/graphify/enable", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "enabled"

    # Verify enabled status appears in list
    skill = client.get("/skills-runtime/graphify", headers=_HEADERS)
    assert skill.json()["enabled"] is True


def test_disable_skill():
    client = TestClient(_make_app())
    client.post("/skills-runtime/headroom/enable", headers=_HEADERS)
    resp = client.post("/skills-runtime/headroom/disable", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


def test_create_tenant_skill():
    client = TestClient(_make_app())
    resp = client.post("/skills-runtime", json={
        "name": "Custom Analyzer",
        "description": "Analyzes data patterns",
        "trigger_hints": ["analyze patterns", "data analysis"],
        "instructions": "Perform pattern analysis on the provided data.",
    }, headers=_HEADERS)
    assert resp.status_code == 200
    assert "skill_id" in resp.json()


def test_execute_skill():
    client = TestClient(_make_app())
    resp = client.post("/skills-runtime/code_review/execute", json={
        "input_context": "def add(a, b): return a+b",
    }, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "execution_id" in data
    assert "output" in data
    assert "success" in data


def test_skill_execution_history():
    client = TestClient(_make_app())
    client.post("/skills-runtime/headroom/execute", json={"input_context": "Test input"}, headers=_HEADERS)

    resp = client.get("/skills-runtime/headroom/executions", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_match_trigger():
    client = TestClient(_make_app())
    resp = client.post("/skills-runtime/match-trigger", json={
        "trigger": "review this code for security issues"
    }, headers=_HEADERS)
    assert resp.status_code == 200
    matches = resp.json()["matches"]
    # Should match security_review or code_review
    match_names = [m["name"] for m in matches]
    assert any("Review" in n or "Security" in n for n in match_names)


def test_trigger_match_no_results():
    client = TestClient(_make_app())
    resp = client.post("/skills-runtime/match-trigger", json={
        "trigger": "xyzzy_totally_unknown_trigger_phrase_12345"
    }, headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["matches"] == []


def test_tenant_skill_isolation():
    client = TestClient(_make_app())
    # Create a skill for tenant A
    create = client.post("/skills-runtime", json={
        "name": "Tenant A Private Skill",
        "description": "Only for tenant A",
    }, headers=_HEADERS)
    skill_id = create.json()["skill_id"]

    # Tenant B should not see it
    resp = client.get(f"/skills-runtime/{skill_id}", headers=_HEADERS_B)
    assert resp.status_code == 404


def test_platform_skills_visible_to_all_tenants():
    client = TestClient(_make_app())
    resp_a = client.get("/skills-runtime/graphify", headers=_HEADERS)
    resp_b = client.get("/skills-runtime/graphify", headers=_HEADERS_B)
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_a.json()["skill_id"] == resp_b.json()["skill_id"]


def test_enable_status_is_per_tenant():
    client = TestClient(_make_app())
    # Tenant A enables graphify
    client.post("/skills-runtime/graphify/enable", headers=_HEADERS)

    # Tenant B should see it as disabled
    skill_b = client.get("/skills-runtime/graphify", headers=_HEADERS_B)
    assert skill_b.json()["enabled"] is False

    # Tenant A should see it as enabled
    skill_a = client.get("/skills-runtime/graphify", headers=_HEADERS)
    assert skill_a.json()["enabled"] is True
