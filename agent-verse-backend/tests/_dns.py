"""Deterministic DNS for unit tests that exercise SSRF-guarded endpoints.

The SSRF guard resolves every outbound host and fails closed on a DNS error.
Unit tests that only exercise endpoint logic (outbound HTTP is mocked) must not
depend on real DNS: under machine load a lookup of e.g. ``api.github.com``
timed out and the guard correctly returned 400, failing unrelated tests.
"""

from __future__ import annotations

import ipaddress

import pytest

PUBLIC_DOC_IP = "93.184.216.34"
_INTERNAL_NAMES = {
    "metadata.google.internal": "169.254.169.254",
    "metadata": "169.254.169.254",
}


def stub_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """IP literals resolve to themselves; localhost/metadata names stay internal;
    every other hostname resolves to a public documentation address."""
    import app.net.ssrf_guard as guard

    def _resolve(host: str) -> list[str]:
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            pass
        name = host.lower().rstrip(".")
        # Keep internal names internal so SSRF refusals are still exercised.
        if name == "localhost" or name.endswith(".localhost"):
            return ["127.0.0.1"]
        if name in _INTERNAL_NAMES:
            return [_INTERNAL_NAMES[name]]
        return [PUBLIC_DOC_IP]

    monkeypatch.setattr(guard, "_resolve_host", _resolve)
