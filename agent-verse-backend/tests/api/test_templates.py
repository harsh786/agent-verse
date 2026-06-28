"""Tests for /templates endpoints."""
from __future__ import annotations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.templates import router as templates_router, _TemplateStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-tmpl", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "ak_test_templates123"
_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(templates_router)
    app.state.template_store = _TemplateStore()
    # Override the module-level store with test store
    import app.api.templates as _mod
    _mod.template_store = app.state.template_store
    return app


def test_list_templates_empty() -> None:
    client = TestClient(_make_app())
    resp = client.get("/templates", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_template_201() -> None:
    client = TestClient(_make_app())
    resp = client.post("/templates", json={
        "name": "Deploy Service", "description": "Deploys a service",
        "goal_text": "Deploy {{service}} to {{environment}}",
        "domain": "devops",
    }, headers=_HEADERS)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Deploy Service"
    # Parameters should be auto-extracted
    param_names = [p["name"] for p in data["parameters"]]
    assert "service" in param_names
    assert "environment" in param_names


def test_instantiate_template() -> None:
    app = _make_app()
    client = TestClient(app)
    create_resp = client.post("/templates", json={
        "name": "Test", "goal_text": "Run tests for {{repo}} on branch {{branch}}",
    }, headers=_HEADERS)
    tmpl_id = create_resp.json()["id"]

    inst_resp = client.post(f"/templates/{tmpl_id}/instantiate", json={
        "parameters": {"repo": "my-app", "branch": "main"},
        "submit": False,
    }, headers=_HEADERS)
    assert inst_resp.status_code == 200
    data = inst_resp.json()
    assert data["instantiated_goal"] == "Run tests for my-app on branch main"


def test_instantiate_fails_on_missing_required_param() -> None:
    client = TestClient(_make_app())
    create_resp = client.post("/templates", json={
        "name": "T", "goal_text": "Do {{action}} on {{target}}",
    }, headers=_HEADERS)
    tmpl_id = create_resp.json()["id"]

    resp = client.post(f"/templates/{tmpl_id}/instantiate", json={"parameters": {"action": "deploy"}}, headers=_HEADERS)
    assert resp.status_code == 422


def test_delete_template_204() -> None:
    app = _make_app()
    client = TestClient(app)
    tmpl_id = client.post("/templates", json={"name": "To Delete", "goal_text": "x"}, headers=_HEADERS).json()["id"]
    assert client.delete(f"/templates/{tmpl_id}", headers=_HEADERS).status_code == 204
    assert client.get(f"/templates/{tmpl_id}", headers=_HEADERS).status_code == 404


def test_tenant_isolation() -> None:
    ctx_a = TenantContext(tenant_id="ta", plan=PlanTier.FREE, api_key_id="ka")
    ctx_b = TenantContext(tenant_id="tb", plan=PlanTier.FREE, api_key_id="kb")
    app = FastAPI()
    store = _TemplateStore()

    async def _resolve(key: str) -> TenantContext | None:
        if key == "ka":
            return ctx_a
        if key == "kb":
            return ctx_b
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(templates_router)
    import app.api.templates as _mod
    _mod.template_store = store
    client = TestClient(app)
    tmpl_id = client.post("/templates", json={"name": "A", "goal_text": "x"}, headers={"X-API-Key": "ka"}).json()["id"]
    assert client.get(f"/templates/{tmpl_id}", headers={"X-API-Key": "kb"}).status_code == 404
    assert client.get("/templates", headers={"X-API-Key": "kb"}).json() == []
