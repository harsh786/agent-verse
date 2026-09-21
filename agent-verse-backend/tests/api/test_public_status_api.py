"""Tests for the public status page API (app/api/public_status.py).

GET /status requires no authentication (bypassed by TenantMiddleware's
_BYPASS_PREFIXES, but we test the router in isolation to keep this focused).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.public_status import router as status_router


def _make_app(health_registry: object | None) -> FastAPI:
    app = FastAPI()
    app.include_router(status_router)
    app.state.health_registry = health_registry
    return app


def test_status_no_registry_defaults_operational() -> None:
    client = TestClient(_make_app(health_registry=None), raise_server_exceptions=False)
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "operational"
    assert body["components"] == {"api": {"status": "operational"}}
    assert body["page_title"] == "AgentVerse System Status"
    assert "timestamp" in body


def test_status_all_healthy() -> None:
    registry = MagicMock()
    registry.run_all = AsyncMock(
        return_value={
            "database": SimpleNamespace(healthy=True, latency_ms=12.345),
            "redis": SimpleNamespace(healthy=True, latency_ms=1.0),
        }
    )
    client = TestClient(_make_app(health_registry=registry), raise_server_exceptions=False)
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "operational"
    assert body["components"]["database"] == {"status": "operational", "latency_ms": 12.35}
    assert body["components"]["redis"]["status"] == "operational"


def test_status_one_unhealthy_marks_degraded() -> None:
    registry = MagicMock()
    registry.run_all = AsyncMock(
        return_value={
            "database": SimpleNamespace(healthy=True, latency_ms=5.0),
            "queue": SimpleNamespace(healthy=False, latency_ms=999.0),
        }
    )
    client = TestClient(_make_app(health_registry=registry), raise_server_exceptions=False)
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["components"]["queue"]["status"] == "degraded"
    assert body["components"]["database"]["status"] == "operational"


def test_status_registry_raises_returns_unknown() -> None:
    registry = MagicMock()
    registry.run_all = AsyncMock(side_effect=RuntimeError("registry exploded"))
    client = TestClient(_make_app(health_registry=registry), raise_server_exceptions=False)
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unknown"
    assert body["components"] == {"api": {"status": "unknown"}}


def test_status_missing_latency_defaults_to_zero() -> None:
    """A result missing/None latency_ms should round to 0.0, not raise."""
    registry = MagicMock()
    registry.run_all = AsyncMock(
        return_value={"cache": SimpleNamespace(healthy=True, latency_ms=None)}
    )
    client = TestClient(_make_app(health_registry=registry), raise_server_exceptions=False)
    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json()["components"]["cache"]["latency_ms"] == 0.0


def test_status_result_missing_healthy_attr_defaults_true() -> None:
    """getattr(result, "healthy", True) — a bare object with no attrs is treated healthy."""
    registry = MagicMock()
    registry.run_all = AsyncMock(return_value={"weird": object()})
    client = TestClient(_make_app(health_registry=registry), raise_server_exceptions=False)
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "operational"
    assert body["components"]["weird"]["status"] == "operational"
