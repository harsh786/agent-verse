"""Tests for P2.10: Async GDPR export and consent management."""
import pytest
from fastapi.testclient import TestClient


def _get_all_paths(app) -> list:
    """Recursively collect all route paths from a FastAPI app.

    Works with both old (path attribute on Route) and new (_IncludedRouter)
    FastAPI/Starlette route structures.
    """
    from fastapi.openapi.utils import get_openapi
    try:
        schema = get_openapi(title="test", version="0.1", routes=app.routes)
        return list(schema.get("paths", {}).keys())
    except Exception:
        pass
    # Fallback: traverse _IncludedRouter objects
    paths = []
    for route in app.routes:
        # Direct APIRoute
        p = str(getattr(route, "path", ""))
        if p:
            paths.append(p)
        # _IncludedRouter wrapping
        orig = getattr(route, "original_router", None)
        if orig is not None:
            for r in getattr(orig, "routes", []):
                rp = str(getattr(r, "path", ""))
                if rp:
                    paths.append(rp)
    return paths


def test_compliance_export_start_endpoint_exists():
    from app.main import create_app
    app = create_app()
    paths = _get_all_paths(app)
    assert any("export/start" in r for r in paths), (
        f"/compliance/export/start endpoint must exist. Found: {[p for p in paths if 'compliance' in p or 'export' in p]}"
    )


def test_consent_record_endpoint_exists():
    from app.main import create_app
    app = create_app()
    paths = _get_all_paths(app)
    assert any("consent" in r for r in paths), (
        f"/compliance/consent endpoint must exist. Found: {[p for p in paths if 'compliance' in p or 'consent' in p]}"
    )


def test_gdpr_export_async():
    from app.main import create_app
    app = create_app()
    client = TestClient(app)
    r = client.post("/tenants/signup", json={"name": "GDPRTest", "email": "g@test.com"})
    if r.status_code not in (200, 201):
        pytest.skip("signup failed")
    h = {"X-API-Key": r.json().get("api_key", "")}
    resp = client.post("/compliance/export/start", headers=h)
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "pending"


def test_consent_record_and_revoke():
    from app.main import create_app
    app = create_app()
    client = TestClient(app)
    r = client.post("/tenants/signup", json={"name": "ConsentTest", "email": "c@test.com"})
    if r.status_code not in (200, 201):
        pytest.skip("signup failed")
    h = {"X-API-Key": r.json().get("api_key", "")}

    # Record consent
    resp = client.post(
        "/compliance/consent",
        json={"purpose": "analytics", "legal_basis": "consent"},
        headers=h,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "consent_id" in data
    assert data["purpose"] == "analytics"

    # Revoke consent
    resp2 = client.delete("/compliance/consent/analytics", headers=h)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "revoked"


def test_mock_server_importable():
    import sys
    sys.path.insert(0, "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-sdk-python")
    from agentverse.mock_server import MockServer
    server = MockServer(port=8099)
    assert server.port == 8099
    assert server._api_key.startswith("mock_")


def test_cli_has_logs_command():
    import inspect
    import sys
    sys.path.insert(0, "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-sdk-python")
    from agentverse import cli
    src = inspect.getsource(cli)
    assert "def logs" in src, "CLI must have 'logs' command"
    assert "def simulate" in src, "CLI must have 'simulate' command"


def test_migration_0042_exists():
    import os
    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend"
        "/app/db/migrations/versions"
    )
    assert any("0042" in f for f in files)

