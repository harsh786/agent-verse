"""UNWIRED-SERVERS: Expensify, Grafana and Looker are real tenant built-ins.

The three modules had working implementations but were never wired, read their
credentials from the platform env at IMPORT time, and Looker cached its API
token in one module-level dict shared by every caller. They are now wired like
the other built-ins: tenant credentials only (read per call), egress-checked and
pinned, catalog entries, and Looker's token cache is per connection.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.mcp.catalog import CONNECTOR_CATALOG
from app.mcp.servers.registry_wiring import get_builtin_server_configs

IDS = ("builtin-expensify", "builtin-grafana", "builtin-looker")


def _cfg(server_id: str) -> dict[str, Any]:
    (cfg,) = [c for c in get_builtin_server_configs() if c["server_id"] == server_id]
    return cfg


@pytest.mark.parametrize("server_id", IDS)
def test_wired_as_tenant_scoped_builtin(server_id: str) -> None:
    cfg = _cfg(server_id)
    assert cfg["requires_env"]
    assert getattr(cfg["handler"], "_tenant_scoped", False)


@pytest.mark.parametrize("server_id", IDS)
def test_catalog_entry(server_id: str) -> None:
    assert any(spec.builtin_server_id == server_id for spec in CONNECTOR_CATALOG)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    import app.net.ssrf_guard as guard

    monkeypatch.setattr(guard, "_resolve_host", lambda host: ["93.184.216.34"])
    for name in (
        "GRAFANA_URL",
        "GRAFANA_API_KEY",
        "LOOKER_BASE_URL",
        "LOOKER_CLIENT_ID",
        "LOOKER_CLIENT_SECRET",
        "EXPENSIFY_PARTNER_USER_ID",
        "EXPENSIFY_PARTNER_USER_SECRET",
    ):
        monkeypatch.setenv(
            name, f"https://platform.invalid/{name}" if "URL" in name else "PLATFORM"
        )
    requests: list[httpx.Request] = []

    async def _send(self: Any, request: httpx.Request, **kw: Any) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/login"):
            body = parse_qs(request.content.decode())
            return httpx.Response(
                200,
                json={"access_token": f"tok-{body['client_id'][0]}", "token_ttl": 3600},
                request=request,
            )
        return httpx.Response(200, json=[], request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", _send)
    return requests


def _no_platform(requests: list[httpx.Request]) -> None:
    for r in requests:
        blob = f"{r.url} {dict(r.headers)} {r.content!r}"
        assert "PLATFORM" not in blob and "platform.invalid" not in blob


async def test_grafana_uses_the_connection_credentials(sent: list[httpx.Request]) -> None:
    creds = {"url": "https://grafana.tenant.example", "api_key": "glsa_tenant"}

    result = await _cfg("builtin-grafana")["handler"](
        "grafana_list_dashboards", {}, credentials=creds
    )

    assert "error" not in result
    (request,) = sent
    assert request.url.host == "grafana.tenant.example"
    assert request.headers["authorization"] == "Bearer glsa_tenant"
    _no_platform(sent)


async def test_expensify_uses_the_connection_credentials(sent: list[httpx.Request]) -> None:
    creds = {"partner_user_id": "tenant-partner", "partner_user_secret": "tenant-secret"}

    await _cfg("builtin-expensify")["handler"]("expensify_get_reports", {}, credentials=creds)

    (request,) = sent
    job = json.loads(parse_qs(request.content.decode())["requestJobDescription"][0])
    assert job["credentials"] == {
        "partnerUserID": "tenant-partner",
        "partnerUserSecret": "tenant-secret",
    }
    _no_platform(sent)


async def test_looker_token_is_never_shared_between_connections(
    sent: list[httpx.Request],
) -> None:
    handler = _cfg("builtin-looker")["handler"]
    base = {"base_url": "https://acme.looker.example"}

    await handler(
        "looker_list_dashboards", {}, credentials={**base, "client_id": "a", "client_secret": "sa"}
    )
    await handler(
        "looker_list_dashboards", {}, credentials={**base, "client_id": "b", "client_secret": "sb"}
    )

    api_calls = [r for r in sent if not r.url.path.endswith("/login")]
    assert [r.headers["authorization"] for r in api_calls] == ["token tok-a", "token tok-b"]
    _no_platform(sent)


@pytest.mark.parametrize("server_id", IDS)
async def test_without_credentials_nothing_is_sent(
    sent: list[httpx.Request], server_id: str
) -> None:
    tool = _cfg(server_id)["tool_definitions"][0]
    args = dict.fromkeys(tool["parameters"].get("required", []), "1")

    result = await _cfg(server_id)["handler"](tool["name"], args, credentials={})

    assert "error" in result
    assert sent == []
