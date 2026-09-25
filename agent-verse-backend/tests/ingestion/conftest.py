"""Shared fixtures for connector unit tests.

Connector unit tests exercise pagination, cursor handling and document shaping
with ``httpx.AsyncClient`` patched out — no request ever leaves the process. They
still run through the real egress guard
(:func:`app.ingestion.connector_egress.assert_source_url`), which fails closed
when a hostname cannot be resolved. That is the correct production behaviour, but
it would make these tests depend on live DNS for their placeholder hosts
(``acme.atlassian.net``, ``es``, ``example.com``), and an offline or
DNS-restricted CI box would fail them for reasons that have nothing to do with
what they assert.

So the placeholder hosts go on the operator allowlist for the duration of these
tests — the same mechanism an on-prem deployment uses for a LAN-hosted Jira,
turned on explicitly and enumerated by name. Tests that assert the guard itself
live in ``test_connector_ssrf_egress.py``; they deliberately do **not** use this
fixture and target literal internal IPs, which need no DNS.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

# Placeholder hosts the connector unit tests point their fake transports at.
_TEST_SOURCE_HOSTS = (
    "acme.atlassian.net",
    "x.atlassian.net",
    "example.com",
    "other.com",
    "es",
    "arxiv.org",
    "www.w3.org",
    "gitlab.com",
    "dev.service-now.com",
    "acme.service-now.com",
)


@pytest.fixture(autouse=True)
def _allow_connector_test_hosts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv(
        "INGESTION_INTERNAL_SOURCE_ALLOWLIST", ",".join(_TEST_SOURCE_HOSTS)
    )
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
