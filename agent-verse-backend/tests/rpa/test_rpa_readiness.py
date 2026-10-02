"""RPA-06: a deployment that needs a browser reports unready without one."""

from __future__ import annotations

import sys

import pytest

from app.rpa import readiness


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "_verified_executable", None)


@pytest.mark.parametrize(
    ("env", "environment", "expected"),
    [
        ("", "development", False),
        ("", "production", True),
        ("", "staging", True),
        ("false", "production", False),
        ("true", "development", True),
    ],
)
def test_browser_required(
    env: str, environment: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("RPA_BROWSER_REQUIRED", env)
    monkeypatch.setenv("ENVIRONMENT", environment)
    get_settings.cache_clear()
    try:
        assert readiness.browser_required() is expected
    finally:
        monkeypatch.setenv("ENVIRONMENT", "development")
        get_settings.cache_clear()


async def test_missing_playwright_fails_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    with pytest.raises(RuntimeError, match="playwright is not installed"):
        await readiness.check_browser_available()


def test_health_reports_the_missing_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    async def _missing() -> None:
        raise RuntimeError("Chromium is not installed")

    monkeypatch.setenv("RPA_BROWSER_REQUIRED", "true")
    monkeypatch.setattr(readiness, "check_browser_available", _missing)
    app = create_app(manage_pools=False)
    resp = TestClient(app).get("/health")
    assert resp.status_code == 503
    assert resp.json()["checks"]["rpa_browser"]["status"] == "down"
