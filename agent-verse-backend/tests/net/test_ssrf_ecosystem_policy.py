"""Every public SSRF-guard entry point follows ALLOW_PRIVATE_NETWORK_ACCESS.

Owner decision (2026-10-07, DEC-SSRF): every egress check in the ecosystem allows
private / internal networks while ``ALLOW_PRIVATE_NETWORK_ACCESS`` is on (the
default in every environment); cloud metadata, link-local, 0.0.0.0 and multicast
stay blocked; ``ALLOW_PRIVATE_NETWORK_ACCESS=false`` restores public-only.

Each entry point below is exercised with 10.0.0.5 / 192.168.63.104 / 127.0.0.1 /
169.254.169.254 under both flag values. Targets are literal IPs (no DNS) and no
socket is opened: the pinned backends / websocket get a fake inner connector.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from app.net import ssrf_guard

PRIVATE_IPS = ["10.0.0.5", "192.168.63.104", "127.0.0.1"]
METADATA_IP = "169.254.169.254"


# ── entry points: each returns True when the target is ALLOWED ──────────────


def _assert_public_url(ip: str) -> bool:
    try:
        ssrf_guard.assert_public_url(f"http://{ip}:8080/x", context="eco")
    except ssrf_guard.SSRFError:
        return False
    return True


def _assert_public_url_async(ip: str) -> bool:
    try:
        asyncio.run(ssrf_guard.assert_public_url_async(f"http://{ip}/", context="eco"))
    except ssrf_guard.SSRFError:
        return False
    return True


def _is_public_url(ip: str) -> bool:
    return ssrf_guard.is_public_url(f"http://{ip}/")


def _is_ssrf_blocked(ip: str) -> bool:
    return not ssrf_guard.is_ssrf_blocked(f"https://{ip}/")


def _resolve_and_check_host(ip: str) -> bool:
    try:
        return ssrf_guard.resolve_and_check_host(ip) == [ip]
    except ssrf_guard.SSRFError:
        return False


class _FakeResponse:
    is_redirect = False


class _FakeAsyncClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def request(self, method: str, url: str, **_: Any) -> _FakeResponse:
        self.calls.append(url)
        return _FakeResponse()


def _request_public(ip: str) -> bool:
    client = _FakeAsyncClient()
    try:
        asyncio.run(ssrf_guard.request_public(client, "GET", f"http://{ip}/", context="eco"))
    except ssrf_guard.SSRFError:
        assert client.calls == []  # refused before any request
        return False
    return client.calls == [f"http://{ip}/"]


class _FakeInnerAsync:
    async def connect_tcp(self, host: str, port: int, **_: Any) -> tuple[str, int]:
        return host, port


class _FakeInnerSync:
    def connect_tcp(self, host: str, port: int, **_: Any) -> tuple[str, int]:
        return host, port


def _pinned_async_backend(ip: str) -> bool:
    backend = ssrf_guard.PinnedNetworkBackend()
    backend._inner = _FakeInnerAsync()  # type: ignore[assignment]
    try:
        return asyncio.run(backend.connect_tcp(ip, 80)) == (ip, 80)
    except ssrf_guard.SSRFError:
        return False


def _pinned_sync_backend(ip: str) -> bool:
    backend = ssrf_guard.PinnedSyncNetworkBackend()
    backend._inner = _FakeInnerSync()  # type: ignore[assignment]
    try:
        return bool(backend.connect_tcp(ip, 80) == (ip, 80))
    except ssrf_guard.SSRFError:
        return False


def _connect_public_websocket(ip: str) -> bool:
    import websockets

    dialled: list[str] = []

    async def fake_connect(uri: str, *, host: str, proxy: Any, **_: Any) -> str:
        dialled.append(host)
        return "ws"

    original = websockets.connect
    websockets.connect = fake_connect  # type: ignore[assignment]
    try:
        asyncio.run(ssrf_guard.connect_public_websocket(f"ws://{ip}:9000/mcp"))
    except ssrf_guard.SSRFError:
        return False
    finally:
        websockets.connect = original  # type: ignore[assignment]
    return dialled == [ip]


def _browser_guard(ip: str) -> bool:
    from app.net.browser_guard import browser_url_block_reason

    return asyncio.run(browser_url_block_reason(f"http://{ip}/")) == ""


def _workflow_guard(ip: str) -> bool:
    from app.workflow.security import SSRFBlockedError, SSRFGuard

    try:
        SSRFGuard().validate(f"http://{ip}/hook")
    except SSRFBlockedError:
        return False
    return True


def _agent_http_tool(ip: str) -> bool:
    from app.tools.http_tool import _is_blocked

    return not _is_blocked(f"http://{ip}/")


def _connector_source_url(ip: str) -> bool:
    from app.ingestion.connector_egress import ConnectorEgressBlockedError, assert_source_url

    try:
        assert_source_url(f"http://{ip}:9200/", context="eco")
    except ConnectorEgressBlockedError:
        return False
    return True


def _connector_source_host(ip: str) -> bool:
    from app.ingestion.connector_egress import ConnectorEgressBlockedError, assert_source_host

    try:
        assert_source_host(ip, 5432, context="eco")
    except ConnectorEgressBlockedError:
        return False
    return True


def _source_config_egress(ip: str) -> bool:
    from app.ingestion.connector_egress import ConnectorEgressBlockedError
    from app.ingestion.source_egress_policy import assert_source_config_egress

    try:
        assert_source_config_egress(
            "postgresql", {"dsn": f"postgresql://u:p@{ip}:5432/db", "host": ip}
        )
    except ConnectorEgressBlockedError:
        return False
    return True


def _mcp_builtin_connect(ip: str) -> bool:
    from app.mcp.servers.egress import checked_addresses

    try:
        return checked_addresses(ip, 443) == [ip]
    except ssrf_guard.SSRFError:
        return False


def _model_endpoint(ip: str) -> bool:
    from app.ai_router.model_endpoints import ModelEndpointError, check_model_endpoint

    try:
        check_model_endpoint(f"http://{ip}:30080/v1")
    except ModelEndpointError:
        return False
    return True


def _tenant_llm_base_url(ip: str) -> bool:
    from app.providers.tenant_provider import TenantProviderError, _assert_tenant_base_url_allowed

    try:
        _assert_tenant_base_url_allowed(f"http://{ip}:8000/v1")
    except TenantProviderError:
        return False
    return True


ENTRY_POINTS: dict[str, Callable[[str], bool]] = {
    "assert_public_url": _assert_public_url,
    "assert_public_url_async": _assert_public_url_async,
    "is_public_url": _is_public_url,
    "is_ssrf_blocked": _is_ssrf_blocked,
    "resolve_and_check_host": _resolve_and_check_host,
    "request_public": _request_public,
    "PinnedNetworkBackend": _pinned_async_backend,
    "PinnedSyncNetworkBackend": _pinned_sync_backend,
    "connect_public_websocket": _connect_public_websocket,
    "browser_url_block_reason": _browser_guard,
    "workflow.SSRFGuard": _workflow_guard,
    "http_tool._is_blocked": _agent_http_tool,
    "connector_egress.assert_source_url": _connector_source_url,
    "connector_egress.assert_source_host": _connector_source_host,
    "source_egress_policy.assert_source_config_egress": _source_config_egress,
    "mcp.servers.egress.checked_addresses": _mcp_builtin_connect,
    "model_endpoints.check_model_endpoint": _model_endpoint,
    "tenant_provider.base_url": _tenant_llm_base_url,
}


@pytest.fixture
def flag(monkeypatch: pytest.MonkeyPatch) -> Callable[[bool], None]:
    def _set(on: bool) -> None:
        monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true" if on else "false")
        monkeypatch.delenv("ALLOW_LINK_LOCAL_NETWORK_ACCESS", raising=False)
        monkeypatch.delenv("TENANT_LLM_ALLOWED_PRIVATE_HOSTS", raising=False)
        monkeypatch.delenv("RPA_SSRF_ALLOWED_DOMAINS", raising=False)

    return _set


@pytest.mark.parametrize("ip", PRIVATE_IPS)
@pytest.mark.parametrize("name", list(ENTRY_POINTS))
def test_private_ip_allowed_with_the_flag_on(flag: Any, name: str, ip: str) -> None:
    flag(True)
    assert ENTRY_POINTS[name](ip) is True, f"{name} refused {ip} with the flag on"


@pytest.mark.parametrize("ip", PRIVATE_IPS)
@pytest.mark.parametrize("name", list(ENTRY_POINTS))
def test_private_ip_refused_with_the_flag_off(flag: Any, name: str, ip: str) -> None:
    flag(False)
    assert ENTRY_POINTS[name](ip) is False, f"{name} allowed {ip} with the flag off"


@pytest.mark.parametrize("on", [True, False])
@pytest.mark.parametrize("name", list(ENTRY_POINTS))
def test_metadata_ip_refused_under_both_flag_values(flag: Any, name: str, on: bool) -> None:
    flag(on)
    assert ENTRY_POINTS[name](METADATA_IP) is False, f"{name} allowed {METADATA_IP}"


@pytest.mark.parametrize("on", [True, False])
def test_is_metadata_host_only_names_the_never_reachable(flag: Any, on: bool) -> None:
    flag(on)
    assert ssrf_guard.is_metadata_host(METADATA_IP) is True
    assert ssrf_guard.is_metadata_host("metadata.google.internal") is True
    for ip in PRIVATE_IPS:
        assert ssrf_guard.is_metadata_host(ip) is False


# ── metadata embedded in IPv6 translation / tunnel forms ────────────────────
# With the flag on ``::/0`` is reachable; these used to pass and would reach
# 169.254.169.254 through a NAT64 gateway / 6to4 relay / Teredo / SIIT.

EMBEDDED_METADATA = [
    "http://[64:ff9b::a9fe:a9fe]/",  # NAT64 well-known prefix
    "http://[64:ff9b:1::a9fe:a9fe]/",  # NAT64 local-use prefix
    "http://[2002:a9fe:a9fe::1]/",  # 6to4
    "http://[2001:0:4136:e378:8000:63bf:5601:5601]/",  # Teredo, client 169.254.169.254
    "http://[::a9fe:a9fe]/",  # IPv4-compatible
    "http://[::ffff:0:a9fe:a9fe]/",  # SIIT IPv4-translated
    "http://[64:ff9b::6464:64c8]/",  # NAT64 of Alibaba 100.100.100.200
    "http://[64:ff9b::0:0]/",  # NAT64 of 0.0.0.0
]


@pytest.mark.parametrize("on", [True, False])
@pytest.mark.parametrize("url", EMBEDDED_METADATA)
def test_metadata_embedded_in_ipv6_stays_blocked(flag: Any, url: str, on: bool) -> None:
    flag(on)
    with pytest.raises(ssrf_guard.SSRFError):
        ssrf_guard.assert_public_url(url, context="eco")


def test_embedded_private_ipv4_follows_the_flag(flag: Any) -> None:
    flag(True)
    assert ssrf_guard.assert_public_url("http://[64:ff9b::a00:5]/")  # NAT64 of 10.0.0.5
    flag(False)
    with pytest.raises(ssrf_guard.SSRFError):
        ssrf_guard.assert_public_url("http://[64:ff9b::a00:5]/")


def test_embedded_metadata_blocked_even_for_an_allowlisted_name(
    flag: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    flag(True)
    monkeypatch.setattr(ssrf_guard, "_resolve_host", lambda _h: ["64:ff9b::a9fe:a9fe"])
    with pytest.raises(ssrf_guard.SSRFError):
        ssrf_guard.assert_public_url("http://lan.example/", allowed_domains=["lan.example"])
    with pytest.raises(ssrf_guard.SSRFError):
        ssrf_guard.assert_public_url("http://lan.example/")


def test_public_ipv6_is_not_caught_by_the_embedded_check(flag: Any) -> None:
    flag(False)
    assert ssrf_guard.assert_public_url("http://[2606:4700:4700::1111]/")  # Cloudflare DNS
    assert ssrf_guard.assert_public_url("http://[2001:4860:4860::8888]/")  # Google DNS
