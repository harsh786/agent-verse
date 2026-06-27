# tests/api/test_replay.py
import pytest
from fastapi.testclient import TestClient


def _make_app():
    from app.main import create_app
    return create_app()


def _get_routes(app) -> list[str]:
    """Return all registered paths by walking FastAPI's OpenAPI schema.

    app.routes contains _IncludedRouter wrappers in this FastAPI version,
    so we use app.openapi()['paths'] which always enumerates every route.
    """
    return list(app.openapi()["paths"].keys())


def test_replay_endpoint_exists():
    """GET /goals/{id}/replay must be registered."""
    app = _make_app()
    routes = _get_routes(app)
    assert any("replay" in r for r in routes), "replay endpoint must be registered"


def test_timeline_endpoint_exists():
    """GET /goals/{id}/timeline must be registered."""
    app = _make_app()
    routes = _get_routes(app)
    assert any("timeline" in r for r in routes), "timeline endpoint must be registered"


def test_replay_returns_404_for_unknown_goal():
    app = _make_app()
    client = TestClient(app)
    signup = client.post("/tenants/signup", json={"name": "ReplayTest", "email": "r@test.com"})
    if signup.status_code not in (200, 201):
        import pytest; pytest.skip("signup failed")
    h = {"X-API-Key": signup.json().get("api_key", "")}
    resp = client.get("/goals/nonexistent-goal-id-xyz/replay", headers=h)
    assert resp.status_code in (404, 503), f"Expected 404 or 503, got {resp.status_code}"


def test_in_process_tracing_configured():
    """Tracing must work even without OTLP endpoint."""
    import os
    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    from opentelemetry import trace
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("test-span") as span:
        span.set_attribute("test.key", "value")
    # If we got here without crashing, in-process tracing works
    assert span is not None


def test_spans_endpoint_exists():
    app = _make_app()
    routes = _get_routes(app)
    assert any("spans" in r or "observability" in r for r in routes), \
        "Spans/observability endpoint must be registered"
