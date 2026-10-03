"""SSRF-02: an operator-allowlisted host that does not resolve is refused.

``assert_public_url`` returned ``[]`` (nothing checked) for an allowlisted
name it could not resolve, trusting every caller to use the pinned client.
A caller that connects on its own (a driver, a plain client) would then
resolve the name itself — to whatever it points at by then. It now fails
closed, and the pinned backends raise ``SSRFError`` (they hit a bare
``assert`` on the empty address list before).
"""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.net.ssrf_guard as g
from app.net.ssrf_guard import (
    PinnedNetworkBackend,
    PinnedSyncNetworkBackend,
    SSRFError,
    assert_public_url,
)


def _unresolvable(host: str) -> list[str]:
    raise socket.gaierror("Name or service not known")


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "_resolve_host", _unresolvable)


def test_unresolvable_allowlisted_host_is_refused() -> None:
    with pytest.raises(SSRFError, match="cannot resolve"):
        assert_public_url("http://db.corp.example/x", allowed_domains=["corp.example"])


async def test_pinned_async_backend_refuses_with_ssrf_error() -> None:
    backend = PinnedNetworkBackend(allowed_domains=["corp.example"])
    backend._inner = AsyncMock()  # type: ignore[assignment]
    with pytest.raises(SSRFError):
        await backend.connect_tcp("db.corp.example", 5432)
    backend._inner.connect_tcp.assert_not_called()


def test_pinned_sync_backend_refuses_with_ssrf_error() -> None:
    backend = PinnedSyncNetworkBackend(allowed_domains=["corp.example"])
    backend._inner = MagicMock()  # type: ignore[assignment]
    with pytest.raises(SSRFError):
        backend.connect_tcp("db.corp.example", 5432)
    backend._inner.connect_tcp.assert_not_called()


def test_resolvable_allowlisted_private_host_still_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda h: ["10.1.2.3"])
    assert assert_public_url("http://db.corp.example/", allowed_domains=["corp.example"]) == [
        "10.1.2.3"
    ]
