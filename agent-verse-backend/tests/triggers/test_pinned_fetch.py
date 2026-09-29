"""Regression: API-poll and RSS triggers connect to the address they validated.

``fetch_json`` / ``fetch_rss_entries`` checked the tenant URL with
``assert_public_url`` and then called ``httpx.stream`` (a second DNS lookup —
DNS rebinding). They now stream through the synchronous pinned client.
"""

from __future__ import annotations

import pytest

import app.net.ssrf_guard as g
from app.net.ssrf_guard import PinnedSyncNetworkBackend, SSRFError, public_client
from app.triggers.polling import fetch_json
from app.triggers.rss import fetch_rss_entries
from tests._pinning import install_connect_spy


def test_sync_public_client_uses_the_pinned_backend() -> None:
    with public_client(timeout=5.0) as client:
        backend = client._transport._pool._network_backend
        assert isinstance(backend, PinnedSyncNetworkBackend)
        assert client.follow_redirects is False


def test_sync_backend_refuses_a_name_that_now_resolves_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda host: ["127.0.0.1"])
    backend = PinnedSyncNetworkBackend()

    class _Inner:
        called = False

        def connect_tcp(self, *a: object, **kw: object) -> None:
            _Inner.called = True

    backend._inner = _Inner()  # type: ignore[assignment]
    with pytest.raises(SSRFError):
        backend.connect_tcp("rebind.example", 443)
    assert _Inner.called is False


def test_api_poll_connects_via_pinned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = install_connect_spy(monkeypatch)
    with pytest.raises(SSRFError):
        fetch_json("https://rebind.example/status")
    assert spy.dialed == ["rebind.example"]


def test_rss_connects_via_pinned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = install_connect_spy(monkeypatch)
    with pytest.raises(SSRFError):
        fetch_rss_entries("https://rebind.example/feed.xml")
    assert spy.dialed == ["rebind.example"]
