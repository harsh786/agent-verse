"""The agent-facing http_request tool must not reach internal addresses.

Regressions: only the literal hostname was checked (a name RESOLVING to an
internal IP passed), and redirects were followed automatically (a public URL
could 302 into the metadata service).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.tools.http_tool import HttpRequestTool


@pytest.mark.asyncio
async def test_hostname_resolving_to_internal_ip_is_blocked() -> None:
    with patch("app.net.ssrf_guard._resolve_host", return_value=["169.254.169.254"]):
        out = await HttpRequestTool().execute(url="https://innocent.example.com/latest")
    assert "Blocked" in out["error"]


@pytest.mark.asyncio
async def test_redirect_into_internal_address_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    real_client = httpx.AsyncClient

    def _client(**kw: Any) -> httpx.AsyncClient:
        kw.pop("follow_redirects", None)
        return real_client(transport=httpx.MockTransport(handler), follow_redirects=False, **kw)

    with (
        patch("app.net.ssrf_guard._resolve_host", return_value=["93.184.216.34"]),
        patch("app.tools.http_tool.httpx.AsyncClient", side_effect=_client),
    ):
        out = await HttpRequestTool().execute(url="https://public.example.com/")
    assert out == {"error": "Blocked: redirect to an internal address."}


@pytest.mark.asyncio
async def test_public_redirect_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, json={"ok": True})

    real_client = httpx.AsyncClient

    def _client(**kw: Any) -> httpx.AsyncClient:
        kw.pop("follow_redirects", None)
        return real_client(transport=httpx.MockTransport(handler), follow_redirects=False, **kw)

    with (
        patch("app.net.ssrf_guard._resolve_host", return_value=["93.184.216.34"]),
        patch("app.tools.http_tool.httpx.AsyncClient", side_effect=_client),
    ):
        out = await HttpRequestTool().execute(url="https://public.example.com/start")
    assert out["status_code"] == 200 and out["body"] == {"ok": True}
