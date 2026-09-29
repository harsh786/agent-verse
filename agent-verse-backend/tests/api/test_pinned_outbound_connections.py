"""Regression: connector test probes and knowledge URL ingest are rebinding-safe.

Each site checked a tenant URL with ``assert_public_url`` and then fetched it
with a plain ``httpx.AsyncClient`` (a second DNS lookup). They now connect via
``public_async_client`` — the connect-time check sees the rebinding answer.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.net.ssrf_guard as g
from app.mcp.registry import MCPServerConfig
from tests._pinning import install_connect_spy
from tests.api.test_connectors_comprehensive2 import _CTX, _VALID_KEY, _make_app, _make_registry


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("github", "https://rebind.example/api/v3"),
        ("jira", "https://rebind.example"),
        ("gitlab", "https://rebind.example"),
        ("custom thing", "https://rebind.example/api"),
    ],
)
def test_connector_test_probe_connects_via_pinned_client(
    monkeypatch: pytest.MonkeyPatch, name: str, url: str
) -> None:
    spy = install_connect_spy(monkeypatch)
    reg = _make_registry()
    sid = asyncio.run(
        reg.register(
            MCPServerConfig(name=name, url=url, auth_type="none"), tenant_ctx=_CTX
        )
    )
    client = TestClient(_make_app(reg))
    resp = client.post(f"/connectors/{sid}/test", headers={"X-API-Key": _VALID_KEY})
    body = resp.json()
    assert body["status"] == "failed"
    assert spy.dialed == ["rebind.example"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["web", "github"])
async def test_knowledge_url_ingest_connects_via_pinned_client(
    monkeypatch: pytest.MonkeyPatch, source_type: str
) -> None:
    from app.api.knowledge import _fetch_url_content

    spy = install_connect_spy(monkeypatch)
    with pytest.raises(HTTPException):
        await _fetch_url_content("https://rebind.example/page", source_type)
    assert spy.dialed == ["rebind.example"]


@pytest.mark.asyncio
async def test_knowledge_url_ingest_revalidates_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.knowledge import _fetch_url_content

    monkeypatch.setattr(g, "_resolve_host", lambda host: ["93.184.216.34"])
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://site.example/page").mock(
            return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        )
        internal = mock.get("http://169.254.169.254/").mock(
            return_value=httpx.Response(200, text="<title>secret</title>")
        )
        with pytest.raises(HTTPException) as exc_info:
            await _fetch_url_content("https://site.example/page", "web")
    assert not internal.called
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_knowledge_url_ingest_follows_public_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.knowledge import _fetch_url_content

    monkeypatch.setattr(g, "_resolve_host", lambda host: ["93.184.216.34"])
    with respx.mock:
        respx.get("http://site.example/page").mock(
            return_value=httpx.Response(301, headers={"location": "https://site.example/page"})
        )
        respx.get("https://site.example/page").mock(
            return_value=httpx.Response(200, text="<title>Hi</title><p>body</p>")
        )
        content, metadata = await _fetch_url_content("http://site.example/page", "web")
    assert "body" in content
    assert metadata["title"] == "Hi"
