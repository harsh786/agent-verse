"""BUILTIN-PINNING: on a tenant call every built-in connection dials the address
that was checked for it — HTTP (httpcore), PostgreSQL, MySQL, Redis, Snowflake.

The endpoint was checked when read, then the driver resolved the name again:
a short-TTL DNS answer could flip from the checked public address to
127.0.0.1 / 10.x / 169.254.169.254 between the check and the connect.
"""

from __future__ import annotations

import socket
import sys
import types
from typing import Any

import httpx
import pytest

from app.mcp.servers import egress
from app.mcp.servers.credentials import tenant_scope
from app.mcp.servers.registry_wiring import get_builtin_server_configs

PUBLIC = "93.184.216.34"


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Controllable resolver for the SSRF guard (what a check sees)."""
    import app.net.ssrf_guard as guard

    answers: dict[str, list[str]] = {}
    monkeypatch.setattr(guard, "_resolve_host", lambda host: answers.get(host, [PUBLIC]))
    return answers


def _handler(server_id: str) -> Any:
    (cfg,) = [c for c in get_builtin_server_configs() if c["server_id"] == server_id]
    return cfg["handler"]


# ── HTTP ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def dialled(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the address httpcore actually dials (instead of connecting)."""
    egress.install_http_pinning()
    hosts: list[str] = []

    async def _fake_connect(self: Any, host: str, port: int, *a: Any, **kw: Any) -> Any:
        import httpcore

        hosts.append(host)
        raise httpcore.ConnectError("test: no network")

    monkeypatch.setitem(egress._ORIGINAL, "async_tcp", _fake_connect)
    return hosts


async def test_http_connect_dials_the_checked_ip(
    dns: dict[str, list[str]], dialled: list[str]
) -> None:
    with tenant_scope({"token": "t"}):
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("https://api.vendor.example/v1/x")
    assert dialled == [PUBLIC]


async def test_http_rebinding_to_private_is_refused_at_connect(
    dns: dict[str, list[str]], dialled: list[str]
) -> None:
    dns["api.vendor.example"] = ["10.0.0.5"]
    with tenant_scope({"token": "t"}):
        async with httpx.AsyncClient() as client:
            with pytest.raises(Exception, match=r"(?i)ssrf|blocked"):
                await client.get("https://api.vendor.example/v1/x")
    assert dialled == []


async def test_http_outside_a_tenant_call_is_unchanged(
    dns: dict[str, list[str]], dialled: list[str]
) -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://api.vendor.example/v1/x")
    assert dialled == ["api.vendor.example"]


async def test_http_builtin_handler_is_pinned(
    dns: dict[str, list[str]], dialled: list[str]
) -> None:
    dns["api.hubapi.com"] = ["169.254.169.254"]
    result = await _handler("builtin-hubspot")(
        "hubspot_list_contacts", {}, credentials={"api_key": "k"}
    )
    assert "error" in result
    assert dialled == []


async def test_unix_socket_is_refused_on_a_tenant_call() -> None:
    egress.install_http_pinning()
    transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
    with tenant_scope({}):
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(Exception, match=r"(?i)unix socket"):
                await client.get("http://localhost/containers/json")


# ── PostgreSQL ───────────────────────────────────────────────────────────────


async def test_postgres_dials_the_checked_ip_with_hostname_tls(
    dns: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncpg

    seen: list[dict[str, Any]] = []

    async def _connect(**kwargs: Any) -> Any:
        seen.append(kwargs)
        raise ConnectionRefusedError("test")

    monkeypatch.setattr(asyncpg, "connect", _connect)
    url = "postgresql://u:p@db.tenant.example:5432/app?sslmode=verify-full"
    await _handler("builtin-postgres")("postgres_list_tables", {}, credentials={"url": url})

    (kwargs,) = seen
    assert PUBLIC in kwargs["dsn"] and "db.tenant.example" not in kwargs["dsn"]
    assert kwargs["ssl"].check_hostname is True


async def test_postgres_rebinding_to_private_never_reaches_the_driver(
    dns: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncpg

    seen: list[Any] = []

    async def _connect(**kwargs: Any) -> Any:
        seen.append(kwargs)

    monkeypatch.setattr(asyncpg, "connect", _connect)
    dns["db.tenant.example"] = [PUBLIC]
    # The credential read passes; the connect-time resolution answers privately.
    calls = {"n": 0}

    def _flip(host: str) -> list[str]:
        calls["n"] += 1
        return [PUBLIC] if calls["n"] == 1 else ["127.0.0.1"]

    import app.net.ssrf_guard as guard

    monkeypatch.setattr(guard, "_resolve_host", _flip)
    result = await _handler("builtin-postgres")(
        "postgres_list_tables", {}, credentials={"url": "postgresql://u:p@db.tenant.example/app"}
    )
    assert "error" in result
    assert seen == []


# ── MySQL ────────────────────────────────────────────────────────────────────


async def test_mysql_dials_the_checked_ip(
    dns: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    async def _connect(**kwargs: Any) -> Any:
        seen.append(kwargs)
        raise ConnectionRefusedError("test")

    fake = types.ModuleType("aiomysql")
    fake.connect = _connect  # type: ignore[attr-defined]
    fake.DictCursor = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiomysql", fake)

    await _handler("builtin-mysql")(
        "mysql_query", {"sql": "SELECT 1"}, credentials={"url": "mysql://u:p@db.tenant.example/app"}
    )

    assert [k["host"] for k in seen] == [PUBLIC]


# ── Redis ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "tls"),
    [("redis://cache.tenant.example:6379/1", False), ("rediss://cache.tenant.example/0", True)],
)
async def test_redis_dials_the_checked_ip(
    dns: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch, url: str, tls: bool
) -> None:
    import asyncio

    seen: list[dict[str, Any]] = []

    async def _open_connection(**kwargs: Any) -> Any:
        seen.append(kwargs)
        raise ConnectionRefusedError("test")

    monkeypatch.setattr(asyncio, "open_connection", _open_connection)
    result = await _handler("builtin-redis")("redis_get", {"key": "k"}, credentials={"url": url})

    assert "error" in result
    assert seen and seen[0]["host"] == PUBLIC
    assert (seen[0].get("server_hostname") == "cache.tenant.example") is tls


# ── Snowflake ────────────────────────────────────────────────────────────────


def _fake_snowflake(monkeypatch: pytest.MonkeyPatch, connect: Any) -> None:
    connector = types.ModuleType("snowflake.connector")
    connector.connect = connect  # type: ignore[attr-defined]
    connector.DictCursor = object  # type: ignore[attr-defined]
    package = types.ModuleType("snowflake")
    package.connector = connector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "snowflake", package)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)


async def test_snowflake_account_host_is_pinned(
    dns: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved: list[Any] = []

    def _connect(**kwargs: Any) -> Any:
        host = f"{kwargs['account']}.snowflakecomputing.com"
        resolved.append(socket.getaddrinfo(host, 443)[0][4][0])
        raise ConnectionRefusedError("test")

    _fake_snowflake(monkeypatch, _connect)
    creds = {"snowflake_account": "acme-x1", "user": "u", "password": "p"}

    await _handler("builtin-snowflake")("snowflake_query", {"sql": "SELECT 1"}, credentials=creds)

    assert resolved == [PUBLIC]


async def test_snowflake_rejects_a_hostname_shaped_account(
    dns: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[Any] = []
    _fake_snowflake(monkeypatch, lambda **kw: called.append(kw))
    creds = {"snowflake_account": "x/../evil", "user": "u", "password": "p"}

    result = await _handler("builtin-snowflake")(
        "snowflake_query", {"sql": "SELECT 1"}, credentials=creds
    )

    assert "account identifier" in result["error"]
    assert called == []
