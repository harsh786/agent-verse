"""Readiness check: a deployment that offers RPA/perception must have a real browser.

Without Playwright (or its Chromium) every rpa_* call fails closed at runtime;
the pod should instead be reported unready so the problem surfaces at deploy
time. Registered on /health and /health/ready when
:func:`browser_required` is true.
"""

from __future__ import annotations

import os

_verified_executable: str | None = None


def browser_required() -> bool:
    """``RPA_BROWSER_REQUIRED`` (true/false); default: every environment but development."""
    raw = os.getenv("RPA_BROWSER_REQUIRED", "").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    from app.core.config import get_settings

    return str(get_settings().environment).strip().lower() != "development"


async def check_browser_available() -> None:
    """Raise unless Playwright imports and its Chromium executable exists.

    A positive result is cached for the process (the image does not lose its
    browser at runtime); a failure is re-probed on every call.
    """
    global _verified_executable
    if _verified_executable is not None:
        return
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is not installed (install the 'browser' extra)") from exc
    pw = await async_playwright().start()
    try:
        path = pw.chromium.executable_path
    finally:
        await pw.stop()
    if not path or not os.path.exists(path):
        raise RuntimeError("Chromium is not installed (run `playwright install chromium`)")
    _verified_executable = path
