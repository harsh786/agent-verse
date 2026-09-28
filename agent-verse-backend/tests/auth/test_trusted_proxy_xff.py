"""X-Forwarded-For is trusted only from explicitly configured proxies.

Regression: ``_get_client_ip`` auto-trusted XFF from ANY loopback / RFC-1918 /
ULA peer and returned the LEFT-most hop. In a cluster every pod has a private
address, so any pod (or anything that could reach the API directly on the pod
network) could claim any source IP — defeating per-tenant IP allowlists and
IP-keyed rate limits. Even behind a real proxy, the left-most hop is whatever
the original client wrote, since proxies append.

Now: XFF is honoured only when the direct peer is in ``TRUSTED_PROXIES``
(IPs/CIDRs; default empty = never trust), and the client is the right-most hop
that is not itself a trusted proxy.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.auth.scope_enforcement import _get_client_ip


def _req(peer: str, xff: str | None = None, real_ip: str | None = None) -> Any:
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = peer
    headers: dict[str, str] = {}
    if xff is not None:
        headers["X-Forwarded-For"] = xff
    if real_ip is not None:
        headers["X-Real-IP"] = real_ip
    req.headers = headers
    return req


@pytest.fixture(autouse=True)
def _no_trusted_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("peer", ["10.0.0.5", "172.16.3.4", "192.168.1.9", "fd00::7"])
def test_private_peer_is_not_auto_trusted(peer: str) -> None:
    assert _get_client_ip(_req(peer, xff="8.8.8.8")) == peer
    assert _get_client_ip(_req(peer, real_ip="8.8.8.8")) == peer


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1"])
def test_untrusted_loopback_proxy_does_not_inherit_loopback_exemption(peer: str) -> None:
    """is_ip_allowed always permits loopback. A local proxy (sidecar) that is not
    configured as trusted must not make every client look like loopback — that
    would silently disable tenant IP allowlists — so the client is 'unknown'."""
    from app.auth.ip_allowlist import is_ip_allowed

    ip = _get_client_ip(_req(peer, xff="8.8.8.8"))
    assert ip == "0.0.0.0"
    assert not is_ip_allowed(ip, ["10.0.0.0/8"])
    assert _get_client_ip(_req(peer, real_ip="8.8.8.8")) == "0.0.0.0"
    # A genuinely local caller (no forwarding header) is still itself.
    assert _get_client_ip(_req(peer)) == peer


def test_rightmost_untrusted_hop_wins_over_spoofed_leftmost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    # Client wrote "6.6.6.6"; the ingress appended the real client 203.0.113.9.
    assert _get_client_ip(_req("10.0.0.5", xff="6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    # Two trusted proxy hops are skipped.
    assert (
        _get_client_ip(_req("10.0.0.5", xff="6.6.6.6, 203.0.113.9, 10.2.2.2")) == "203.0.113.9"
    )


def test_untrusted_peer_xff_ignored_even_when_proxies_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.10")
    assert _get_client_ip(_req("10.0.0.11", xff="1.2.3.4")) == "10.0.0.11"


def test_all_hops_trusted_returns_leftmost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    assert _get_client_ip(_req("10.0.0.5", xff="10.9.9.9, 10.1.1.1")) == "10.9.9.9"


def test_malformed_hop_stops_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    # Garbage is never returned as the client IP; the last trusted hop is.
    assert _get_client_ip(_req("10.0.0.5", xff="not-an-ip, 10.1.1.1")) == "10.1.1.1"


def test_x_real_ip_only_from_trusted_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.5")
    assert _get_client_ip(_req("10.0.0.5", real_ip="198.51.100.7")) == "198.51.100.7"
    assert _get_client_ip(_req("10.0.0.6", real_ip="198.51.100.7")) == "10.0.0.6"


def test_trusted_proxies_read_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    assert Settings().trusted_proxies == ""  # default: trust no proxy
    monkeypatch.setenv("TRUSTED_PROXIES", "192.168.0.0/16, 10.0.0.1")
    assert Settings().trusted_proxies == "192.168.0.0/16, 10.0.0.1"
