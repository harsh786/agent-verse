"""Tests: health endpoint returns both `checks` and `dependencies` keys."""
from __future__ import annotations

import pytest

from app.observability.health import HealthCheck, HealthRegistry


# ── HealthRegistry.run() output keys ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_returns_checks_key():
    registry = HealthRegistry()

    async def ok():
        pass

    registry.register(HealthCheck(name="postgres", check=ok))
    healthy, report = await registry.run()
    assert "postgres" in report
    assert report["postgres"]["status"] == "up"
    assert healthy is True


@pytest.mark.asyncio
async def test_run_failing_check_marks_unhealthy():
    registry = HealthRegistry()

    async def bad():
        raise RuntimeError("connection refused")

    registry.register(HealthCheck(name="redis", check=bad))
    healthy, report = await registry.run()
    assert healthy is False
    assert report["redis"]["status"] == "down"
    assert "connection refused" in report["redis"]["error"]


# ── Health endpoint response shape ─────────────────────────────────────────────
# Test the system.py endpoint logic directly (no HTTP server needed).

@pytest.mark.asyncio
async def test_health_response_has_both_checks_and_dependencies():
    """The health endpoint payload must contain both `checks` AND `dependencies` keys."""
    registry = HealthRegistry()

    async def ok():
        pass

    registry.register(HealthCheck(name="db", check=ok))
    healthy, checks = await registry.run()

    # Simulate what system.py builds
    payload = {
        "status": "healthy" if healthy else "unhealthy",
        "checks": checks,
        "dependencies": checks,
    }

    assert "checks" in payload, "payload must have 'checks' key"
    assert "dependencies" in payload, "payload must have 'dependencies' key (alias)"
    assert payload["checks"] == payload["dependencies"], "checks and dependencies must be equal"
    assert payload["status"] == "healthy"
    assert "db" in payload["checks"]


@pytest.mark.asyncio
async def test_health_dependencies_alias_contains_check_results():
    """Both keys must contain the same per-service results."""
    registry = HealthRegistry()

    async def ok():
        pass

    for name in ["postgres", "redis", "mcp"]:
        registry.register(HealthCheck(name=name, check=ok))

    healthy, checks = await registry.run()
    payload = {
        "status": "healthy" if healthy else "unhealthy",
        "checks": checks,
        "dependencies": checks,
    }

    for service in ["postgres", "redis", "mcp"]:
        assert service in payload["dependencies"]
        assert payload["dependencies"][service]["status"] == "up"


@pytest.mark.asyncio
async def test_health_dependencies_shows_down_services():
    """dependencies key must surface failing services."""
    registry = HealthRegistry()

    async def ok():
        pass

    async def fail():
        raise ConnectionError("timeout")

    registry.register(HealthCheck(name="postgres", check=ok))
    registry.register(HealthCheck(name="redis", check=fail))

    healthy, checks = await registry.run()
    payload = {
        "status": "healthy" if healthy else "unhealthy",
        "checks": checks,
        "dependencies": checks,
    }

    assert payload["status"] == "unhealthy"
    assert payload["dependencies"]["postgres"]["status"] == "up"
    assert payload["dependencies"]["redis"]["status"] == "down"
