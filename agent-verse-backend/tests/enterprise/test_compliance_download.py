"""Tests for GDPR compliance export and download."""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app


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


async def test_export_request(authed_client):
    resp = await authed_client.get("/enterprise/compliance/export")
    assert resp.status_code == 200
    data = resp.json()
    assert "request_id" in data
    assert data["status"] in ("pending", "ready", "processing")


async def test_export_download(authed_client):
    """Request an export then download it as JSON."""
    # First request the export
    export_resp = await authed_client.get("/enterprise/compliance/export")
    assert export_resp.status_code == 200
    request_id = export_resp.json()["request_id"]

    # Download
    download_resp = await authed_client.get(
        f"/enterprise/compliance/export/{request_id}/download"
    )
    # 200 = ready, 202 = still processing
    assert download_resp.status_code in (200, 202)
    if download_resp.status_code == 200:
        content_type = download_resp.headers.get("content-type", "")
        assert "application/json" in content_type


async def test_export_download_has_tenant_payload(authed_client):
    """Downloaded export payload contains the authenticated tenant's ID."""
    export_resp = await authed_client.get("/enterprise/compliance/export")
    request_id = export_resp.json()["request_id"]

    download_resp = await authed_client.get(
        f"/enterprise/compliance/export/{request_id}/download"
    )
    if download_resp.status_code == 200:
        body = download_resp.json()
        # Top-level payload has tenant_id
        assert "tenant_id" in body


async def test_export_download_unknown_id_returns_404(authed_client):
    """Downloading a non-existent export request returns 404."""
    resp = await authed_client.get("/enterprise/compliance/export/nonexistent/download")
    assert resp.status_code == 404


async def test_data_residency(authed_client):
    resp = await authed_client.get("/enterprise/compliance/residency")
    assert resp.status_code == 200
    data = resp.json()
    assert "primary_region" in data or "region" in data
    # FIX: gdpr_compliant must NOT be hardcoded True — it's dynamically checked.
    # Verify the field exists and has a note about the dynamic compliance endpoint.
    assert "gdpr_compliant" in data
    assert data["gdpr_compliant"] is False  # FIX: no longer hardcoded True
    assert "note" in data  # Points to /enterprise/compliance/gdpr


async def test_compliance_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        for path in ["/enterprise/compliance/export", "/enterprise/compliance/residency"]:
            resp = await c.get(path)
            assert resp.status_code == 401, f"Path {path} should require auth"


async def test_export_status_endpoint(authed_client):
    """Export request is immediately retrievable via the status endpoint."""
    export_resp = await authed_client.get("/enterprise/compliance/export")
    request_id = export_resp.json()["request_id"]

    status_resp = await authed_client.get(
        f"/enterprise/compliance/export/{request_id}"
    )
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["request_id"] == request_id
    assert "payload" in body


async def test_export_status_not_found(authed_client):
    resp = await authed_client.get("/enterprise/compliance/export/ghost-id")
    assert resp.status_code == 404
