"""Tests for Phase 9 Agent Lab API."""
import pytest


class TestLabAPI:
    def test_lab_router_importable(self):
        from app.api.lab import router
        assert router is not None

    def test_lab_router_has_run_endpoint(self):
        from app.api.lab import router
        paths = [r.path for r in router.routes]
        assert any("/run" in p for p in paths)

    def test_lab_router_has_tools_endpoint(self):
        from app.api.lab import router
        paths = [r.path for r in router.routes]
        assert any("/tools" in p for p in paths)
