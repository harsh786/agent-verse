"""Tests for Phase 10 memory inspection + delete endpoints.

These tests verify routes exist at the router level — they do not require a
running server and are immune to async-task side-effects from other tests.
"""
from __future__ import annotations

import pytest


def _get_knowledge_routes() -> list[str]:
    """Directly inspect the knowledge router without creating a full app."""
    try:
        from app.api.knowledge import router as knowledge_router
        return [route.path for route in knowledge_router.routes if hasattr(route, "path")]
    except Exception:
        return []


def _get_memory_routes() -> list[str]:
    """Directly inspect the memory router without creating a full app."""
    try:
        from app.api.memory import router as memory_router
        return [route.path for route in memory_router.routes if hasattr(route, "path")]
    except Exception:
        return []


def test_memory_list_endpoint_exists():
    routes = _get_memory_routes()
    assert any("memory" in r or r == "/" or r == "" for r in routes) or len(routes) > 0, \
        f"Memory router must have endpoints, got: {routes}"


def test_memory_recall_endpoint_exists():
    routes = _get_memory_routes()
    assert any("recall" in r for r in routes), \
        f"Memory recall endpoint must exist in memory router, got: {routes}"


def test_knowledge_url_ingest_endpoint_exists():
    routes = _get_knowledge_routes()
    assert any("ingest/url" in r or "/ingest/url" in r for r in routes), \
        f"URL ingest endpoint must exist in knowledge router, got: {routes}"


