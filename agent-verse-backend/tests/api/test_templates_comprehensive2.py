"""Comprehensive tests for /templates API — targets 56% → 80%+ coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.templates as tmpl_module
from app.api.templates import _TemplateStore, router as templates_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-tmpl-comp", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_templates_comp"

_CTX2 = TenantContext(tenant_id="tid-tmpl-other", plan=PlanTier.FREE, api_key_id="kid-2")
_VALID_KEY2 = "av_test_templates_other"


def _make_app() -> tuple[FastAPI, _TemplateStore]:
    """Build app and swap module-level template_store with a fresh instance."""
    store = _TemplateStore()
    # Replace module-level store so the router picks it up
    tmpl_module.template_store = store

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        if key == _VALID_KEY:
            return _CTX
        if key == _VALID_KEY2:
            return _CTX2
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(templates_router)
    return app, store


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


def test_list_templates_requires_auth() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/templates")
    assert resp.status_code == 401


def test_create_template_requires_auth() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/templates", json={"name": "t", "goal_text": "g"})
    assert resp.status_code == 401


def test_get_template_requires_auth() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/templates/some-id")
    assert resp.status_code == 401


def test_instantiate_requires_auth() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/templates/some-id/instantiate", json={})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /templates  (returns plain list)
# ---------------------------------------------------------------------------


def test_list_templates_empty() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/templates", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_templates_returns_created() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    client.post(
        "/templates",
        json={"name": "Deploy Tmpl", "goal_text": "Deploy {{service}} to {{env}}"},
        headers={"X-API-Key": _VALID_KEY},
    )
    resp = client.get("/templates", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "Deploy Tmpl"


def test_list_templates_filter_by_domain() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    client.post(
        "/templates",
        json={"name": "Deploy", "goal_text": "Deploy {{svc}}", "domain": "devops"},
        headers={"X-API-Key": _VALID_KEY},
    )
    client.post(
        "/templates",
        json={"name": "Analyze", "goal_text": "Analyze {{data}}", "domain": "analytics"},
        headers={"X-API-Key": _VALID_KEY},
    )
    resp = client.get("/templates?domain=devops", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "Deploy"


def test_list_templates_tenant_isolation() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    # Create for tenant 1
    client.post(
        "/templates",
        json={"name": "T1 tmpl", "goal_text": "Goal for T1"},
        headers={"X-API-Key": _VALID_KEY},
    )
    # List for tenant 2 — should be empty
    resp = client.get("/templates", headers={"X-API-Key": _VALID_KEY2})
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /templates
# ---------------------------------------------------------------------------


def test_create_template_auto_extract_parameters() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/templates",
        json={
            "name": "Deploy Service",
            "goal_text": "Deploy {{service}} to {{environment}} with version {{version}}",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    param_names = [p["name"] for p in body["parameters"]]
    assert "service" in param_names
    assert "environment" in param_names
    assert "version" in param_names


def test_create_template_explicit_parameters() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/templates",
        json={
            "name": "Custom",
            "goal_text": "Do {{thing}}",
            "parameters": [{"name": "thing", "description": "The thing", "required": True, "default": None}],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["parameters"][0]["description"] == "The thing"


def test_create_template_with_domain() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/templates",
        json={"name": "Infra", "goal_text": "Provision {{resource}}", "domain": "infrastructure"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["domain"] == "infrastructure"


def test_create_template_empty_name_invalid() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/templates",
        json={"name": "", "goal_text": "Something"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_create_template_empty_goal_text_invalid() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/templates",
        json={"name": "Good name", "goal_text": ""},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_create_template_no_placeholders() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/templates",
        json={"name": "Simple", "goal_text": "Run unit tests for the project"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["parameters"] == []


# ---------------------------------------------------------------------------
# GET /templates/{id}
# ---------------------------------------------------------------------------


def test_get_template_success() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/templates",
        json={"name": "Get Me", "goal_text": "Goal {{x}}"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    tmpl_id = created["id"]

    resp = client.get(f"/templates/{tmpl_id}", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Get Me"


def test_get_template_not_found() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/templates/nonexistent-id", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_get_template_wrong_tenant_returns_404() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/templates",
        json={"name": "T1 only", "goal_text": "Goal"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    tmpl_id = created["id"]

    # Tenant 2 should not see tenant 1's template
    resp = client.get(f"/templates/{tmpl_id}", headers={"X-API-Key": _VALID_KEY2})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /templates/{id}
# ---------------------------------------------------------------------------


def test_update_template_success() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/templates",
        json={"name": "Old Name", "goal_text": "Old goal"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    tmpl_id = created["id"]

    resp = client.put(
        f"/templates/{tmpl_id}",
        json={"name": "New Name", "goal_text": "New goal {{param}}"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 204

    # Verify update persisted
    get_resp = client.get(f"/templates/{tmpl_id}", headers={"X-API-Key": _VALID_KEY})
    assert get_resp.json()["name"] == "New Name"


def test_update_template_not_found() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.put(
        "/templates/nonexistent",
        json={"name": "Name", "goal_text": "Goal"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /templates/{id}
# ---------------------------------------------------------------------------


def test_delete_template_success() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/templates",
        json={"name": "Delete Me", "goal_text": "Goal"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    tmpl_id = created["id"]

    resp = client.delete(f"/templates/{tmpl_id}", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204

    # Verify gone
    assert client.get(f"/templates/{tmpl_id}", headers={"X-API-Key": _VALID_KEY}).status_code == 404


def test_delete_template_not_found() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.delete("/templates/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /templates/{id}/instantiate
# ---------------------------------------------------------------------------


def test_instantiate_replaces_parameters() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/templates",
        json={"name": "Deploy", "goal_text": "Deploy {{service}} to {{env}}"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    tmpl_id = created["id"]

    resp = client.post(
        f"/templates/{tmpl_id}/instantiate",
        json={"parameters": {"service": "api", "env": "production"}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["instantiated_goal"] == "Deploy api to production"
    assert body["template_id"] == tmpl_id


def test_instantiate_not_found() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/templates/nonexistent/instantiate",
        json={"parameters": {}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_instantiate_without_submit() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/templates",
        json={"name": "T", "goal_text": "Run {{task}}"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    tmpl_id = created["id"]

    resp = client.post(
        f"/templates/{tmpl_id}/instantiate",
        json={"parameters": {"task": "tests"}, "submit": False},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["instantiated_goal"] == "Run tests"


def test_instantiate_missing_required_param_returns_422() -> None:
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/templates",
        json={"name": "T", "goal_text": "Deploy {{service}}"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    tmpl_id = created["id"]

    # Don't provide required "service" param
    resp = client.post(
        f"/templates/{tmpl_id}/instantiate",
        json={"parameters": {}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_instantiate_with_submit_no_goal_service_returns_503() -> None:
    """submit=True without goal service should return 503."""
    app, _ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    # Create template with no required params
    created = client.post(
        "/templates",
        json={"name": "T", "goal_text": "Run all tests", "parameters": []},
        headers={"X-API-Key": _VALID_KEY},
    ).json()
    tmpl_id = created["id"]

    resp = client.post(
        f"/templates/{tmpl_id}/instantiate",
        json={"parameters": {}, "submit": True},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503
