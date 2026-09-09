"""Opt-in guard for the live-backend integration tests.

``tests/real_integration/test_live_api_comprehensive.py`` hits a running backend
at ``AGENTVERSE_URL`` (default http://localhost:8000) that must be freshly
provisioned and seeded. Running them by accident (e.g. against a stale server) is
worse than not running them — a socket being open on :8000 does not mean the API
is the right, seeded one. So these tests are OPT-IN: they run only when
``AGENTVERSE_LIVE_TESTS`` is set AND the backend is reachable; otherwise the whole
directory is skipped, keeping the default unit/integration tiers green and
hang-free in any environment.
"""

from __future__ import annotations

import os
import socket
import urllib.parse

import pytest

_BASE = os.getenv("AGENTVERSE_URL", "http://localhost:8000")
_OPT_IN = os.getenv("AGENTVERSE_LIVE_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}


def _server_reachable(url: str, *, timeout: float = 0.5) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if _OPT_IN and _server_reachable(_BASE):
        return
    reason = (
        f"live backend not reachable at {_BASE}"
        if _OPT_IN
        else "set AGENTVERSE_LIVE_TESTS=1 (with a seeded server) to run live-API tests"
    )
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "real_integration" in str(item.fspath):
            item.add_marker(skip)
