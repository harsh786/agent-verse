"""Tests for Phase 10 memory inspection + delete endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _make_app():
    from app.main import create_app
    return create_app()


def _get_route_paths(app) -> list[str]:
    """Extract all registered route paths from a FastAPI app via OpenAPI schema."""
    try:
        return list(app.openapi().get("paths", {}).keys())
    except Exception:
        # Fallback: shallow scan of routes
        paths = []
        for r in app.routes:
            p = getattr(r, "path", None)
            if p:
                paths.append(p)
        return paths


def test_memory_list_endpoint_exists():
    app = _make_app()
    routes = _get_route_paths(app)
    assert any("memory" in r for r in routes), "Memory list endpoint must exist"


def test_memory_recall_endpoint_exists():
    app = _make_app()
    routes = _get_route_paths(app)
    memory_routes = [r for r in routes if "memory" in r]
    assert any("recall" in r for r in memory_routes), "Memory recall endpoint must exist"


def test_knowledge_url_ingest_endpoint_exists():
    app = _make_app()
    routes = _get_route_paths(app)
    assert any("ingest/url" in r for r in routes), "URL ingest endpoint must exist"
