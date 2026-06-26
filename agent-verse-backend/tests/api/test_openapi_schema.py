"""Tests that OpenAPI schema includes all registered endpoints."""
from __future__ import annotations

import pytest
from fastapi.openapi.utils import get_openapi
from app.main import create_app


@pytest.fixture(scope="module")
def openapi_schema():
    app = create_app()
    return get_openapi(title=app.title, version=app.version, routes=app.routes)


def test_schema_has_perception_endpoints(openapi_schema):
    paths = openapi_schema["paths"]
    assert "/perception/status" in paths
    assert "/perception/screenshot" in paths
    assert "/perception/analyze" in paths
    assert "/perception/extract" in paths
    assert "/perception/goal-with-image" in paths


def test_schema_has_rpa_endpoints(openapi_schema):
    paths = openapi_schema["paths"]
    assert "/rpa/tools" in paths
    assert "/rpa/execute" in paths
    assert "/rpa/sessions" in paths


def test_schema_has_goals_metrics(openapi_schema):
    paths = openapi_schema["paths"]
    assert "/goals/metrics" in paths
    assert "/goals/{goal_id}/eval" in paths


def test_schema_has_core_endpoints(openapi_schema):
    paths = openapi_schema["paths"]
    for required in ["/goals", "/agents", "/connectors", "/governance/policies", "/health"]:
        assert required in paths, f"Missing required endpoint: {required}"


def test_schema_has_minimum_path_count(openapi_schema):
    """Schema must have at least 65 paths (we have 70+)."""
    assert len(openapi_schema["paths"]) >= 65, (
        f"Only {len(openapi_schema['paths'])} paths found — likely missing routers"
    )


def test_all_endpoints_have_response_schemas(openapi_schema):
    """Every endpoint should define at least one response."""
    paths = openapi_schema["paths"]
    missing = []
    for path, methods in paths.items():
        for method, spec in methods.items():
            if method in ("get", "post", "put", "delete", "patch"):
                if not spec.get("responses"):
                    missing.append(f"{method.upper()} {path}")
    assert not missing, f"Endpoints missing response schemas: {missing}"
