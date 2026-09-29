"""Regression: chat-attachment downloads are rebinding-safe and redirect-checked.

``_download_command_file`` checked the (external, chat-supplied) file URL with
``assert_public_url`` and then fetched it with a plain ``httpx.AsyncClient``.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

import app.net.ssrf_guard as g
from app.gateway.router import _download_command_file
from tests._pinning import install_connect_spy


@pytest.mark.asyncio
async def test_file_download_connects_via_pinned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = install_connect_spy(monkeypatch)
    out = await _download_command_file(SimpleNamespace(url="https://rebind.example/f.pdf"))
    assert out is None
    assert spy.dialed == ["rebind.example"]


@pytest.mark.asyncio
async def test_file_download_redirect_to_internal_host_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda host: ["93.184.216.34"])
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://files.example/f.pdf").mock(
            return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/x"})
        )
        internal = mock.get("http://169.254.169.254/x").mock(
            return_value=httpx.Response(200, content=b"secret")
        )
        out = await _download_command_file(SimpleNamespace(url="https://files.example/f.pdf"))
    assert out is None
    assert not internal.called


@pytest.mark.asyncio
async def test_file_download_follows_public_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda host: ["93.184.216.34"])
    with respx.mock:
        respx.get("https://files.example/f.pdf").mock(
            return_value=httpx.Response(302, headers={"location": "https://cdn.example/f.pdf"})
        )
        respx.get("https://cdn.example/f.pdf").mock(
            return_value=httpx.Response(200, content=b"%PDF-1.4")
        )
        out = await _download_command_file(SimpleNamespace(url="https://files.example/f.pdf"))
    assert out == b"%PDF-1.4"
