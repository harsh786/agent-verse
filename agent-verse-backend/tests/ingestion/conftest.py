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
    # Placeholder database/broker hosts for the host:port connectors (egress-
    # guarded since the second SSRF pass). ``test`` is the RFC 2606 reserved TLD.
    "test",
    "influx",
    "db",
    "db.local",
    "ch.local",
    "h",
    "bad",
)


@pytest.fixture(autouse=True)
def _allow_connector_test_hosts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    import app.net.ssrf_guard as guard

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv(
        "INGESTION_INTERNAL_SOURCE_ALLOWLIST", ",".join(_TEST_SOURCE_HOSTS)
    )
    # An allowlisted name that does not resolve is refused (SSRF-02), so the
    # placeholder hosts resolve to a private test address here — no live DNS.
    real_resolve = guard._resolve_host
    placeholders = {h.lower() for h in _TEST_SOURCE_HOSTS}

    def _resolve(host: str) -> list[str]:
        if host.lower() in placeholders or any(
            host.lower().endswith("." + p) for p in placeholders
        ):
            return ["10.255.0.1"]
        return real_resolve(host)

    monkeypatch.setattr(guard, "_resolve_host", _resolve)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def allow_unpinnable_drivers(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Opt out of strict egress pinning, for tests of drivers that resolve hosts
    themselves (confluent-kafka): with it on they are refused outright."""
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_EGRESS_STRICT_PINNING", "false")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
