"""EGRESS-NET: the operator ingestion allowlist accepts private IP ranges (CIDR).

Testing deployments reach LAN services by private IP (a MongoDB at
172.23.76.120:27017, a MinIO at 192.168.63.104:30900). Listing every IP is
impractical, so ``INGESTION_INTERNAL_SOURCE_ALLOWLIST`` also takes networks like
``192.168.0.0/16``. The policy stays operator-only and fail-closed:

* both halves are needed (``INGESTION_ALLOW_INTERNAL_SOURCES`` + the list);
* the cloud metadata service / link-local / 0.0.0.0 stay unreachable even when a
  network covering them is listed;
* a name whose DNS answer leaves the listed networks is still refused;
* the networks reach only the ingestion allowlist — other callers of the SSRF
  guard (tenant web-search / RPA domains) cannot pass them;
* production refuses network entries (use service hostnames there).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import Settings, get_settings
from app.ingestion import connector_egress as ce
from app.ingestion.connector_egress import (
    ConnectorEgressBlockedError,
    assert_source_dsn,
    assert_source_host,
    assert_source_url,
)
from app.net import ssrf_guard as sg

_RANGES = (
    "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16,0.0.0.0/8,"
    "100.64.0.0/10,192.0.0.0/24,198.18.0.0/15,240.0.0.0/4,255.255.255.255/32,"
    "::1/128,fc00::/7,fe80::/10,::/128"
)


@pytest.fixture
def operator_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", f"minio,{_RANGES}")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


# ── ssrf_guard: allowed_networks ────────────────────────────────────────────


def _nets(*cidrs: str) -> list[sg.IPNetwork]:
    return sg.parse_allowed_networks(",".join(cidrs))


def test_literal_ip_inside_an_allowed_network_is_allowed() -> None:
    assert sg.assert_public_url(
        "http://192.168.63.104:30900/", allowed_networks=_nets("192.168.0.0/16")
    ) == ["192.168.63.104"]


def test_private_ip_outside_the_listed_networks_stays_blocked() -> None:
    with pytest.raises(sg.SSRFError):
        sg.assert_public_url("http://10.0.0.5/", allowed_networks=_nets("192.168.0.0/16"))
    with pytest.raises(sg.SSRFError):
        sg.assert_public_url("http://192.168.63.104/")  # no networks: unchanged


def test_metadata_and_link_local_stay_blocked_even_when_listed() -> None:
    every = _nets("0.0.0.0/0", "169.254.0.0/16", "0.0.0.0/8", "::/0")
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.10.10/",
        "http://0.0.0.0/",
        "http://[fe80::1]/",
        "http://metadata.google.internal/",
    ):
        with pytest.raises(sg.SSRFError):
            sg.assert_public_url(url, allowed_networks=every)


def test_hostname_resolving_inside_the_network_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["172.23.76.120"])
    assert sg.assert_public_url("http://mongo.lan/", allowed_networks=_nets("172.16.0.0/12")) == [
        "172.23.76.120"
    ]


def test_hostname_with_an_answer_outside_the_networks_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-rebinding: EVERY address must be public or inside a listed network."""
    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["172.23.76.120", "10.9.9.9"])
    with pytest.raises(sg.SSRFError):
        sg.assert_public_url("http://mongo.lan/", allowed_networks=_nets("172.16.0.0/12"))


def test_connect_time_check_honours_the_networks() -> None:
    assert sg.resolve_and_check_host(
        "192.168.63.104", allowed_networks=_nets("192.168.0.0/16")
    ) == ["192.168.63.104"]
    with pytest.raises(sg.SSRFError):
        sg.resolve_and_check_host("192.168.63.104")


def test_unparseable_network_entries_never_widen_the_policy() -> None:
    assert sg.parse_allowed_networks("192.168.0.0/99,not-a-net/8,minio,10.1.2.3") == []
    assert [str(n) for n in sg.parse_allowed_networks(" 192.168.1.7/16 ,fc00::/7")] == [
        "192.168.0.0/16",
        "fc00::/7",
    ]


# ── connector_egress: operator allowlist with networks ──────────────────────


def test_mongodb_on_a_private_ip_is_allowed_when_its_range_is_listed(
    operator_env: pytest.MonkeyPatch,
) -> None:
    assert assert_source_host("172.23.76.120", 27017, context="mongodb") == ["172.23.76.120"]
    assert_source_dsn("mongodb://172.23.76.120:27017/app?directConnection=true", context="mongodb")
    assert_source_url("http://192.168.63.104:30900/", context="s3")


def test_metadata_stays_blocked_with_every_range_listed(
    operator_env: pytest.MonkeyPatch,
) -> None:
    for url in ("http://169.254.169.254/latest/meta-data/", "http://0.0.0.0:27017/"):
        with pytest.raises(ConnectorEgressBlockedError):
            assert_source_url(url, context="test")


def test_ranges_need_the_flag_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGESTION_ALLOW_INTERNAL_SOURCES", raising=False)
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "172.16.0.0/12")
    get_settings.cache_clear()
    try:
        with pytest.raises(ConnectorEgressBlockedError):
            assert_source_host("172.23.76.120", 27017, context="mongodb")
    finally:
        get_settings.cache_clear()


def test_pinned_source_client_carries_the_networks(operator_env: pytest.MonkeyPatch) -> None:
    client = ce.source_client()
    backend = client._transport._pool._network_backend
    assert [str(n) for n in backend._allowed_networks][:4] == [
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    ]


# ── production refuses ranges ───────────────────────────────────────────────


def test_production_refuses_ip_ranges_in_the_allowlist() -> None:
    with pytest.raises(ValueError, match="service hostnames"):
        Settings(
            environment="production",
            ingestion_allow_internal_sources=True,
            ingestion_internal_source_allowlist="mongo-svc,192.168.0.0/16",
        )


def test_production_keeps_hostname_allowlisting() -> None:
    s = Settings(
        environment="production",
        ingestion_allow_internal_sources=True,
        ingestion_internal_source_allowlist="mongo-svc,minio.storage.svc",
    )
    assert s.ingestion_internal_source_allowlist == "mongo-svc,minio.storage.svc"


# ── the generic HTTP connector follows the same operator policy ─────────────


def _http_config(url: str) -> object:
    from app.ingestion.source_config import SourceConfig, SourceFamily

    return SourceConfig(
        source_id="s-http",
        tenant_id="t",
        name="internal api",
        family=SourceFamily.WEB,
        source_type="http",
        connection_config={"url": url},
    )


async def test_http_connector_reaches_a_listed_private_range(
    operator_env: pytest.MonkeyPatch,
) -> None:
    from app.ingestion.connectors.http_connector import HttpApiConnector

    connector = HttpApiConnector()

    async def _fetch(config: object, *, cursor: str | None) -> object:
        return [{"id": 1, "title": "t"}]

    operator_env.setattr(connector, "_fetch", _fetch)
    health = await connector.validate_connection(_http_config("http://192.168.63.104:8080/api"))
    assert health.ok, health.error
    blocked = await connector.validate_connection(_http_config("http://169.254.169.254/latest/"))
    assert not blocked.ok and "blocked" in (blocked.error or "")
    pinned = connector_client_backend()
    assert [str(n) for n in pinned._allowed_networks][:1] == ["127.0.0.0/8"]


def connector_client_backend() -> object:
    """The pinned backend of the client the HTTP connector fetches with."""
    from app.ingestion.connectors import http_connector

    client = http_connector.source_client()
    return client._transport._pool._network_backend


# ── DEC-SSRF: with ALLOW_PRIVATE_NETWORK_ACCESS on, CIDR entries are moot ────


def test_production_accepts_ip_ranges_while_private_access_is_on() -> None:
    s = Settings(
        environment="production",
        allow_private_network_access=True,
        ingestion_allow_internal_sources=True,
        ingestion_internal_source_allowlist="mongo-svc,192.168.0.0/16",
    )
    assert "192.168.0.0/16" in s.ingestion_internal_source_allowlist


def test_production_refuses_ip_ranges_while_private_access_is_off() -> None:
    with pytest.raises(ValueError, match="service hostnames"):
        Settings(
            environment="production",
            allow_private_network_access=False,
            ingestion_allow_internal_sources=True,
            ingestion_internal_source_allowlist="mongo-svc,192.168.0.0/16",
        )
