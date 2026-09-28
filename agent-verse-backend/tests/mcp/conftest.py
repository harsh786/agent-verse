"""Shared fixtures for MCP tests."""

from __future__ import annotations

import pytest

# Placeholder hosts used by OAuth tests. The SSRF guard resolves hosts and fails
# closed on DNS errors, so resolve these to a public documentation address.
_PLACEHOLDER_HOSTS = {"auth.example.com", "auth.test", "provider.example"}


@pytest.fixture(autouse=True)
def _resolve_placeholder_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.net.ssrf_guard as guard

    real = guard._resolve_host

    def _resolve(host: str) -> list[str]:
        if host in _PLACEHOLDER_HOSTS:
            return ["93.184.216.34"]
        return real(host)

    monkeypatch.setattr(guard, "_resolve_host", _resolve)
