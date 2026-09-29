"""SSRF: connector update/import, connector health task, /enterprise/saml/test.

Regressions:
* PUT /connectors/{id} and POST /connectors/import-openapi had no SSRF guard;
  registration checked only ``url`` (not auth_config['url'] used by built-ins).
* check_mcp_health GET {base_url}/health with follow_redirects=True, unchecked.
* /enterprise/saml/test GET any caller URL with follow_redirects=True.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.api.test_connectors_extra2 import _VALID_KEY, _make_app

_H = {"X-API-Key": _VALID_KEY}
_META = "http://169.254.169.254/latest/meta-data/"


@pytest.fixture(autouse=True)
def _dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hostnames resolve to a public IP; literal internal IPs stay blocked."""
    monkeypatch.setattr("app.net.ssrf_guard._resolve_host", lambda host: ["93.184.216.34"])


def _register(client: TestClient) -> str:
    r = client.post(
        "/connectors",
        json={"name": "Thing", "url": "https://api.example.com/mcp", "auth_type": "bearer"},
        headers=_H,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["server_id"])


def test_connector_update_rejects_internal_url() -> None:
    client = TestClient(_make_app())
    sid = _register(client)
    r = client.put(
        f"/connectors/{sid}",
        json={"name": "Thing", "url": _META, "auth_type": "bearer"},
        headers=_H,
    )
    assert r.status_code == 400
    assert "SSRF" in r.json()["detail"]


def test_connector_update_rejects_internal_auth_config_url() -> None:
    client = TestClient(_make_app())
    sid = _register(client)
    r = client.put(
        f"/connectors/{sid}",
        json={
            "name": "Thing",
            "url": "https://api.example.com/mcp",
            "auth_type": "basic",
            "auth_config": {"url": "http://10.0.0.8:8080"},
        },
        headers=_H,
    )
    assert r.status_code == 400


def test_connector_register_rejects_internal_auth_config_url() -> None:
    client = TestClient(_make_app())
    r = client.post(
        "/connectors",
        json={
            "name": "Jira",
            "url": "builtin://",
            "auth_type": "basic",
            "auth_config": {"url": "http://127.0.0.1:9000", "api_token": "t"},
        },
        headers=_H,
    )
    assert r.status_code == 400


def test_openapi_import_rejects_internal_base_url() -> None:
    client = TestClient(_make_app())
    spec = {"openapi": "3.0.0", "info": {"title": "x", "version": "1"}, "paths": {}}
    r = client.post(
        "/connectors/import-openapi",
        json={"openapi_spec": json.dumps(spec), "base_url": "http://192.168.1.10/api"},
        headers=_H,
    )
    assert r.status_code == 400


# ── /enterprise/saml/test ─────────────────────────────────────────────────────


def _saml_client() -> TestClient:
    from tests.api.test_enterprise_coverage_boost import _make_app as _ent_app

    return TestClient(_ent_app(), raise_server_exceptions=False)


def _saml_headers() -> dict[str, str]:
    from tests.api.test_enterprise_coverage_boost import _headers

    return _headers()


def test_saml_test_rejects_internal_url() -> None:
    r = _saml_client().post(
        "/enterprise/saml/test", json={"sso_url": _META}, headers=_saml_headers()
    )
    assert r.status_code == 400


def test_saml_test_revalidates_redirect_hops() -> None:
    calls: list[str] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(302, headers={"location": _META})

    # The pinned-client seam: the route builds its client with
    # public_async_client (never follows redirects itself).
    def _client(**kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_handler), follow_redirects=False, **kw
        )

    with patch("app.net.ssrf_guard.public_async_client", _client):
        r = _saml_client().post(
            "/enterprise/saml/test",
            json={"sso_url": "https://idp.example.com/sso"},
            headers=_saml_headers(),
        )
    assert r.status_code == 400
    assert calls == ["https://idp.example.com/sso"]  # the internal hop was never fetched


# ── check_mcp_health ──────────────────────────────────────────────────────────


def test_mcp_health_check_never_requests_internal_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp.registry import MCPServerConfig
    from app.scaling import tasks

    cfg = MCPServerConfig(server_id="s1", name="evil", url=_META, base_url=_META)

    class _R:
        async def scan_iter(self, **kw: Any) -> Any:
            yield "mcp:servers:t1:s1"

        async def get(self, key: str) -> str:
            return cfg.model_dump_json()

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: _R())
    sent: list[str] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        sent.append(str(req.url))
        return httpx.Response(200)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(_handler), **kw)
    )
    out = tasks.check_mcp_health.run()
    assert sent == []
    assert out["results"][0]["status"] == "unreachable"
