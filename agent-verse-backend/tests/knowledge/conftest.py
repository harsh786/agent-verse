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
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", ",".join(_TEST_SOURCE_HOSTS))
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
