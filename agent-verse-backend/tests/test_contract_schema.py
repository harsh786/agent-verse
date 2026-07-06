"""Contract tests: verify the OpenAPI schema is internally consistent."""
import json
import re

import pytest


def test_openapi_schema_importable() -> None:
    """The app must be importable without errors."""
    from app.main import create_app

    app = create_app(manage_pools=False)
    assert app is not None


def test_openapi_schema_has_required_routes() -> None:
    """Critical API routes must be present in the OpenAPI schema."""
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(manage_pools=False)
    client = TestClient(app)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = schema.get("paths", {})
    # Core routes that must exist
    required_routes = ["/goals", "/agents", "/health"]
    for route in required_routes:
        assert any(p.startswith(route) for p in paths), f"Missing route: {route}"


def test_openapi_schema_no_undefined_refs() -> None:
    """OpenAPI schema must not have $ref pointing to undefined components."""
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(manage_pools=False)
    client = TestClient(app)
    response = client.get("/openapi.json")
    schema = response.json()
    schema_text = json.dumps(schema)
    components = schema.get("components", {}).get("schemas", {})
    # Find all $ref values referencing local component schemas
    refs = re.findall(r'"#/components/schemas/([^"]+)"', schema_text)
    for ref in refs:
        assert ref in components, f"Undefined $ref: #/components/schemas/{ref}"


@pytest.mark.parametrize(
    "method,path,expected_status",
    [
        ("GET", "/health", 200),
        ("GET", "/goals", 401),  # Requires auth
        ("GET", "/agents", 401),  # Requires auth
    ],
)
def test_key_endpoints_return_correct_status(method: str, path: str, expected_status: int) -> None:
    """Smoke test: key endpoints respond with expected HTTP status."""
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(manage_pools=False)
    client = TestClient(app)
    response = client.request(method, path)
    assert response.status_code == expected_status, (
        f"{method} {path} returned {response.status_code}, expected {expected_status}"
    )
