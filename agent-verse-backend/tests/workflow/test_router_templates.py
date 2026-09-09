"""Tests for workflow template marketplace router."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.workflow.template_store import SystemTemplate, SystemTemplateStore


def _template(slug: str = "kyc-automation") -> dict:
    return {
        "slug": slug, "name": "KYC Automation",
        "description": "Customer verification", "category": "Financial Services",
        "tags": ["kyc", "compliance"], "version": "1.0.0",
        "author": "AgentVerse Team", "complexity": "medium",
        "popularity_score": 0.0, "sample_input": {}, "definition": {"name": "KYC", "steps": []},
    }


def make_app(store: MagicMock) -> "TestClient":
    from fastapi import FastAPI, Request

    from app.tenancy.context import PlanLimits, PlanTier, TenantContext
    from app.workflow.dsl import WorkflowDefinition
    from app.workflow.router_templates import router

    app = FastAPI()

    class FakeTenant(TenantContext):
        def __init__(self) -> None:
            pass
        tenant_id = "test-tenant"
        plan = PlanTier.FREE
        api_key = "test-key"
        api_key_id = "key-1"
        limits = PlanLimits(60, 25, 3, 2, 1, 3600)

    @app.middleware("http")
    async def inject_state(request: Request, call_next):
        request.app.state.template_store = store
        request.app.state.tenant_context = FakeTenant()
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def _make_template_obj(slug: str = "kyc-automation") -> SystemTemplate:
    raw = _template(slug)
    return SystemTemplate(raw)


@pytest.fixture
def template_store() -> MagicMock:
    store = MagicMock()
    t = _make_template_obj()
    store.list.return_value = ([t], 1)
    store.get.return_value = t
    store.categories.return_value = [
        {"category": "Financial Services", "count": 5},
        {"category": "Engineering", "count": 3},
    ]
    store.fork.return_value = MagicMock(
        id="wf-new", name="KYC Automation", description="", steps=[],
        trigger=None, inputs={}, outputs={}, vars={}, version="1.0.0",
        forked_from="kyc-automation", tags=[], env={}, notifications=[],
        run_labels={}, concurrency=None, error_handling=None,
        callback=None, trigger_transform={},
        requires_publish_approval=False, publish_approved_by=None,
        publish_approved_at=None, run_retention_days=90,
    )
    return store


@pytest.fixture
def client(template_store: MagicMock) -> "TestClient":
    return make_app(template_store)


# ── List templates ────────────────────────────────────────────────────────────


def test_list_templates(client: "TestClient") -> None:
    resp = client.get("/api/v1/workflow-templates")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] == 1


def test_list_templates_by_category(client: "TestClient") -> None:
    resp = client.get("/api/v1/workflow-templates?category=Financial+Services")
    assert resp.status_code == 200


def test_list_templates_pagination(client: "TestClient") -> None:
    resp = client.get("/api/v1/workflow-templates?page=1&per_page=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["per_page"] == 10


# ── Categories ────────────────────────────────────────────────────────────────


def test_list_categories(client: "TestClient") -> None:
    resp = client.get("/api/v1/workflow-templates/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["category"] == "Financial Services"
    assert data[0]["count"] == 5


# ── Get template ──────────────────────────────────────────────────────────────


def test_get_template(client: "TestClient") -> None:
    resp = client.get("/api/v1/workflow-templates/kyc-automation")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "kyc-automation"
    assert data["category"] == "Financial Services"


def test_get_template_not_found(client: "TestClient", template_store: MagicMock) -> None:
    from app.workflow.template_store import TemplateNotFoundError
    template_store.get.side_effect = TemplateNotFoundError("not found")
    resp = client.get("/api/v1/workflow-templates/bad-slug")
    assert resp.status_code == 404


# ── Preview run ───────────────────────────────────────────────────────────────


def test_preview_run(client: "TestClient") -> None:
    resp = client.get("/api/v1/workflow-templates/kyc-automation/preview-run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "kyc-automation"
    assert data["dry_run"] is True
    assert "step_count" in data


def test_preview_run_not_found(client: "TestClient", template_store: MagicMock) -> None:
    from app.workflow.template_store import TemplateNotFoundError
    template_store.get.side_effect = TemplateNotFoundError("not found")
    resp = client.get("/api/v1/workflow-templates/bad/preview-run")
    assert resp.status_code == 404


# ── Fork template ─────────────────────────────────────────────────────────────


def test_fork_template(client: "TestClient") -> None:
    resp = client.post("/api/v1/workflow-templates/kyc-automation/fork", json={})
    assert resp.status_code == 201
    data = resp.json()
    assert "workflow_id" in data
    assert data["forked_from"] == "kyc-automation"


def test_fork_template_with_name_override(client: "TestClient") -> None:
    resp = client.post("/api/v1/workflow-templates/kyc-automation/fork", json={
        "name": "My Custom KYC"
    })
    assert resp.status_code == 201


def test_fork_template_not_found(client: "TestClient", template_store: MagicMock) -> None:
    from app.workflow.template_store import TemplateNotFoundError
    template_store.fork.side_effect = TemplateNotFoundError("not found")
    resp = client.post("/api/v1/workflow-templates/bad/fork", json={})
    assert resp.status_code == 404


# ── Search ────────────────────────────────────────────────────────────────────


def test_search_templates(client: "TestClient") -> None:
    resp = client.get("/api/v1/workflow-templates/search?q=kyc")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["query"] == "kyc"
