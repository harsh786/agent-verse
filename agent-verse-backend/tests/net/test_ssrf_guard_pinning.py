"""Regression tests: SSRF guard pins connections and re-checks redirects.

1. validate-then-connect: ``assert_public_url`` resolved the name, then the HTTP
   client resolved it AGAIN to connect — a DNS answer that flips between the
   two (rebinding) reached 127.0.0.1 / 169.254.169.254. ``PinnedNetworkBackend``
   resolves + checks at connect time and dials the checked IP.
2. The RPA http fetch used ``follow_redirects=True`` after a single up-front
   check, so a public page could 302 to the metadata service.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import app.net.ssrf_guard as g
from app.net.ssrf_guard import PinnedNetworkBackend, SSRFError, public_async_client
from app.rpa.executor import RPAExecutor


def test_public_client_uses_the_pinned_backend() -> None:
    client = public_async_client(timeout=5.0)
    try:
        pool = client._transport._pool  # type: ignore[attr-defined]
        assert isinstance(pool._network_backend, PinnedNetworkBackend)
        assert client.follow_redirects is False
    finally:
        import asyncio

        asyncio.run(client.aclose())


@pytest.mark.asyncio
async def test_connect_refuses_a_name_that_now_resolves_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda h: ["127.0.0.1"])
    backend = PinnedNetworkBackend()
    backend._inner = AsyncMock()  # type: ignore[assignment]
    with pytest.raises(SSRFError):
        await backend.connect_tcp("rebind.example", 443)
    backend._inner.connect_tcp.assert_not_called()


@pytest.mark.asyncio
async def test_connect_dials_the_checked_ip_not_the_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda h: ["93.184.216.34"])
    backend = PinnedNetworkBackend()
    inner = AsyncMock()
    backend._inner = inner  # type: ignore[assignment]
    await backend.connect_tcp("example.com", 443, timeout=3.0)
    args: Any = inner.connect_tcp.await_args
    assert args.args[0] == "93.184.216.34"
    assert args.args[1] == 443


@pytest.mark.asyncio
async def test_connect_honours_the_operator_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda h: ["10.1.2.3"])
    backend = PinnedNetworkBackend(allowed_domains=["corp.example"])
    inner = AsyncMock()
    backend._inner = inner  # type: ignore[assignment]
    await backend.connect_tcp("wiki.corp.example", 80)
    assert inner.connect_tcp.await_args.args[0] == "10.1.2.3"


@pytest.mark.asyncio
async def test_rpa_fetch_does_not_follow_a_redirect_to_metadata() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://1.1.1.1/").mock(
            return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
        )
        meta = mock.get("http://169.254.169.254/").mock(return_value=httpx.Response(200))
        with pytest.raises(SSRFError):
            await RPAExecutor._http_fetch_text("https://1.1.1.1/")
    assert not meta.called
