"""Shared fixtures for the legacy knowledge ingestor tests.

The Confluence/Jira ingestors behind ``/ingest/confluence`` and ``/ingest/jira``
now run the real egress guard (``app.ingestion.connector_egress``), which fails
closed when a hostname cannot be resolved. These tests mock the transport and
point at placeholder hosts, so — exactly like ``tests/ingestion/conftest.py`` —
the placeholders go on the operator allowlist by name for the test's duration.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

_TEST_SOURCE_HOSTS = ("confluence.example.com", "conf.example.com", "jira.example.com")


@pytest.fixture(autouse=True)
def _allow_ingestor_test_hosts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.net.ssrf_guard as guard
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", ",".join(_TEST_SOURCE_HOSTS))
    # An allowlisted name that does not resolve is refused (SSRF-02), so the
    # placeholder hosts resolve to a private test address here — no live DNS
    # (same as tests/ingestion/conftest.py).
    real_resolve = guard._resolve_host
    placeholders = {h.lower() for h in _TEST_SOURCE_HOSTS}

    def _resolve(host: str) -> list[str]:
        if host.lower() in placeholders:
            return ["10.255.0.1"]
        return real_resolve(host)

    monkeypatch.setattr(guard, "_resolve_host", _resolve)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
