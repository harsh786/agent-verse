"""Phase 10 — E2E smoke tests against a live backend.

These tests hit the running backend at BASE_URL (default: http://localhost:8000).
They are skipped automatically when the backend is not reachable.

Run them explicitly:
    pytest tests/e2e/test_smoke.py -m smoke -v

Or against a specific host:
    BASE_URL=https://staging.agentverse.io pytest tests/e2e/test_smoke.py -m smoke
"""
from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.getenv("AGENTVERSE_BASE_URL", "http://localhost:8000")
SKIP_REASON = f"Backend not reachable at {BASE_URL} (start the server first)"

pytestmark = pytest.mark.smoke


# ── Backend availability fixture ──────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=False)
def backend_available() -> bool:
    """Return True if the backend is reachable, else skip."""
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=3.0)
        return r.status_code in (200, 503)
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return False


@pytest.fixture(scope="session")
def http(backend_available: bool) -> httpx.Client:
    if not backend_available:
        pytest.skip(SKIP_REASON)
    return httpx.Client(base_url=BASE_URL, timeout=10.0)


# ── Smoke scenario 1 — health endpoint ───────────────────────────────────────

def test_health_endpoint_returns_200(http: httpx.Client) -> None:
    """GET /health returns 200 or 503 (degraded) with JSON body."""
    r = http.get("/health")
    assert r.status_code in (200, 503), f"Unexpected status: {r.status_code}"
    data = r.json()
    assert isinstance(data, dict)
    assert "status" in data or "checks" in data or "ok" in data


# ── Smoke scenario 2 — unauthenticated request returns 401 ──────────────────

def test_goals_without_auth_returns_401(http: httpx.Client) -> None:
    """GET /v1/goals without API key returns 401."""
    r = http.get("/v1/goals")
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"


# ── Smoke scenario 3 — metrics endpoint ─────────────────────────────────────

def test_metrics_endpoint_accessible(http: httpx.Client) -> None:
    """GET /metrics returns Prometheus text format or JSON."""
    r = http.get("/metrics")
    assert r.status_code in (200, 404), f"Metrics endpoint: {r.status_code}"
    if r.status_code == 200:
        content_type = r.headers.get("content-type", "")
        assert "text/plain" in content_type or "application/json" in content_type


# ── Smoke scenario 4 — OpenAPI schema ────────────────────────────────────────

def test_openapi_schema_accessible(http: httpx.Client) -> None:
    """GET /openapi.json returns valid OpenAPI schema."""
    r = http.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert "openapi" in schema
    assert "paths" in schema
    assert schema["openapi"].startswith("3.")


# ── Smoke scenario 5 — HITL magic link with bad token returns 4xx ────────────

def test_hitl_bad_token_returns_4xx(http: httpx.Client) -> None:
    """GET /v1/hitl/{id}/approve?token=bad returns 4xx (not 500)."""
    r = http.get(
        "/v1/hitl/nonexistent-request-id/approve",
        params={"token": "obviously-invalid-token"},
    )
    assert r.status_code in (400, 401, 403, 404, 410, 422), (
        f"Expected 4xx, got {r.status_code}: {r.text[:100]}"
    )


# ── Smoke scenario 6 — voice status endpoint ─────────────────────────────────

def test_voice_status_requires_auth(http: httpx.Client) -> None:
    """GET /v1/voice/status without auth returns 401."""
    r = http.get("/v1/voice/status")
    assert r.status_code == 401, f"Expected 401, got {r.status_code}"


# ── Smoke scenario 7 — API with valid key returns non-500 ────────────────────

def test_authenticated_request_with_valid_key(http: httpx.Client) -> None:
    """Requests with a well-formed key format return non-500 responses."""
    api_key = os.getenv("AGENTVERSE_API_KEY", "")
    if not api_key:
        pytest.skip("AGENTVERSE_API_KEY not set — skipping authenticated smoke test")

    r = http.get("/v1/goals", headers={"X-API-Key": api_key})
    assert r.status_code in (200, 404), f"Unexpected: {r.status_code}"


# ── Smoke scenario 8 — SSE endpoint content-type ─────────────────────────────

def test_sse_goals_stream_content_type(http: httpx.Client) -> None:
    """GET /v1/goals/{id}/stream returns SSE content type or 401/404."""
    r = http.get(
        "/v1/goals/nonexistent/stream",
        headers={"Accept": "text/event-stream"},
        timeout=2.0,
    )
    if r.status_code == 200:
        assert "text/event-stream" in r.headers.get("content-type", "")
    else:
        assert r.status_code in (401, 404)
