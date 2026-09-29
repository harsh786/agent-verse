"""Test helper: prove a call site connects through the SSRF-pinned client.

``install_connect_spy`` makes the up-front ``assert_public_url`` check see a
public address, then replaces the CONNECT-time check used by
:class:`app.net.ssrf_guard.PinnedNetworkBackend` with a spy that records the
host and rejects it — the DNS-rebinding answer (public at check time, internal
at connect time). A call site built on a plain ``httpx.AsyncClient`` never
reaches the spy (it resolves the name itself), so ``dialed`` stays empty; a
pinned call site records the host and never opens a socket.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

PUBLIC_IP = "93.184.216.34"


@dataclass
class ConnectSpy:
    dialed: list[str] = field(default_factory=list)
    allowlists: list[list[str] | None] = field(default_factory=list)


def install_connect_spy(monkeypatch: pytest.MonkeyPatch) -> ConnectSpy:
    import app.net.ssrf_guard as g

    spy = ConnectSpy()
    monkeypatch.setattr(g, "_resolve_host", lambda host: [PUBLIC_IP])

    def _connect_check(host: str, *, allowed_domains: list[str] | None = None) -> list[str]:
        spy.dialed.append(host)
        spy.allowlists.append(allowed_domains)
        raise g.SSRFError(
            f"SSRF guard [connect]: hostname '{host}' resolved to blocked IP '127.0.0.1'"
        )

    monkeypatch.setattr(g, "resolve_and_check_host", _connect_check)
    return spy
