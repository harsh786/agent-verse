"""conftest for tests/e2e — bypass SSRF guard for mock/test hostnames.

Several e2e tests register connectors pointing at hostnames like
'mock-jira-mcp.local' or 'example.com/mcp' that either do not resolve in the
CI/dev environment (mDNS .local domains) or would be blocked by the SSRF guard
before respx can intercept the call.

The autouse fixture below patches `assert_public_url` to a no-op for all tests
in this package so the guard does not prevent mock HTTP calls.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _bypass_ssrf(monkeypatch):
    """Disable SSRF hostname-resolution guard for e2e tests.

    e2e tests use mock URLs (*.local, non-resolving example sub-domains) and
    rely on respx to intercept the HTTP calls — the SSRF guard must not block
    those requests before respx can handle them.
    """
    import app.mcp.client as _mcp_client
    import app.net.ssrf_guard as _ssrf
    import app.api.connectors as _connectors_api

    noop = lambda *_a, **_kw: None  # noqa: E731
    monkeypatch.setattr(_ssrf, "assert_public_url", noop)
    monkeypatch.setattr(_mcp_client, "assert_public_url", noop)
    monkeypatch.setattr(_connectors_api, "assert_public_url", noop)
