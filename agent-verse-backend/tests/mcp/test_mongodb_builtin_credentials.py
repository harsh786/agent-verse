"""MONGO-CREDS: the MongoDB built-in uses ONLY the calling connector's credentials.

The handler used to read the platform env ``MONGODB_MCP_URL`` and took no
``credentials`` argument, so MCPClient never passed the tenant's connection
string: tenant calls ran against the PLATFORM database (confused deputy).
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPServerConfig
from app.mcp.servers import mongodb_server
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="t-mongo", plan=PlanTier.PROFESSIONAL, api_key_id="k")

PLATFORM_URI = "mongodb://platform-user:platform-pass@8.8.4.4:27017/platform_db"
# Public IP literals: the egress check passes without DNS.
TENANT_URI = "mongodb://tenant-user:tenant-pass@8.8.8.8:27017/tenant_db"


class _FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name

    def count_documents(self, query: dict[str, Any]) -> int:
        return 7


class _FakeDB:
    def __getitem__(self, name: str) -> _FakeCollection:
        return _FakeCollection(name)


class _RecordingClient:
    """Stands in for pymongo.MongoClient and records how it was built."""

    instances: list[_RecordingClient] = []

    def __init__(self, dsn: str, **kwargs: Any) -> None:
        self.dsn = dsn
        self.kwargs = kwargs
        self.db_names: list[str] = []
        self.closed = False
        _RecordingClient.instances.append(self)

    def __getitem__(self, name: str) -> _FakeDB:
        self.db_names.append(name)
        return _FakeDB()

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def recording_client(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingClient]:
    import pymongo

    _RecordingClient.instances = []
    monkeypatch.setattr(pymongo, "MongoClient", _RecordingClient)
    return _RecordingClient


def test_handler_accepts_credentials() -> None:
    assert "credentials" in inspect.signature(mongodb_server.call_tool).parameters


@pytest.mark.asyncio
async def test_platform_env_is_never_used(
    monkeypatch: pytest.MonkeyPatch, recording_client: type[_RecordingClient]
) -> None:
    monkeypatch.setenv("MONGODB_MCP_URL", PLATFORM_URI)

    result = await mongodb_server.call_tool("mongodb_count", {"collection": "c"})

    assert "configure credentials" in result["error"].lower()
    assert recording_client.instances == []


@pytest.mark.asyncio
async def test_tenant_uri_and_auth_options_are_used(
    monkeypatch: pytest.MonkeyPatch, recording_client: type[_RecordingClient]
) -> None:
    monkeypatch.setenv("MONGODB_MCP_URL", PLATFORM_URI)

    result = await mongodb_server.call_tool(
        "mongodb_count",
        {"collection": "c"},
        credentials={"url": TENANT_URI, "auth_source": "admin", "tls": "false"},
    )

    assert result == {"count": 7, "collection": "c"}
    (client,) = recording_client.instances
    assert "8.8.8.8" in client.dsn and "tenant-user" in client.dsn
    assert "8.8.4.4" not in client.dsn
    assert client.kwargs["authSource"] == "admin"
    assert client.kwargs["tls"] is False
    # Single host: no discovery of members the server advertises.
    assert client.kwargs["directConnection"] is True
    assert client.db_names == ["tenant_db"]
    assert client.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        "mongodb://10.0.0.5:27017/db",
        "mongodb://127.0.0.1:27017/db",
        "mongodb://169.254.169.254:27017/db",
        # A replica-set seed list: EVERY member is checked, not just the first.
        "mongodb://8.8.8.8:27017,10.1.2.3:27017/db?replicaSet=rs0",
    ],
)
async def test_private_hosts_are_blocked(
    uri: str, recording_client: type[_RecordingClient]
) -> None:
    result = await mongodb_server.call_tool(
        "mongodb_count", {"collection": "c"}, credentials={"url": uri}
    )

    assert "error" in result
    assert recording_client.instances == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix",
    [
        "tlsCAFile=/etc/passwd",
        "tlsCertificateKeyFile=/etc/ssl/private/key.pem",
        "proxyHost=10.0.0.1&proxyPort=1080",
        "authMechanism=MONGODB-AWS",
        "authMechanism=GSSAPI",
        "authMechanism=MONGODB-OIDC&authMechanismProperties=ENVIRONMENT:azure",
    ],
)
async def test_platform_reaching_uri_options_are_refused(
    suffix: str, recording_client: type[_RecordingClient]
) -> None:
    result = await mongodb_server.call_tool(
        "mongodb_count", {"collection": "c"}, credentials={"url": f"{TENANT_URI}?{suffix}"}
    )

    assert "not allowed" in result["error"]
    assert recording_client.instances == []


@pytest.mark.asyncio
async def test_replica_set_only_selects_listed_members(
    recording_client: type[_RecordingClient],
) -> None:
    uri = "mongodb://u:p@8.8.8.8:27017,8.8.4.4:27018/db?replicaSet=rs0"

    await mongodb_server.call_tool("mongodb_count", {"collection": "c"}, credentials={"url": uri})

    (client,) = recording_client.instances
    assert "directConnection" not in client.kwargs
    selector = client.kwargs["server_selector"]

    class _SD:
        def __init__(self, host: str, port: int) -> None:
            self.address = (host, port)

    listed, advertised = _SD("8.8.8.8", 27017), _SD("10.9.9.9", 27017)
    assert selector([listed, advertised]) == [listed]


@pytest.mark.asyncio
async def test_non_mongodb_uri_is_rejected(recording_client: type[_RecordingClient]) -> None:
    result = await mongodb_server.call_tool(
        "mongodb_count", {"collection": "c"}, credentials={"url": "https://example.com"}
    )

    assert "mongodb://" in result["error"]
    assert recording_client.instances == []


# ── Through MCPClient: the tenant connector's URI reaches the handler ─────────


def _mongo_cfg(url: str) -> MCPServerConfig:
    return MCPServerConfig(
        server_id="builtin-mongodb",
        name="MongoDB",
        url=url,
        builtin_handler=mongodb_server.call_tool,
    )


@pytest.mark.asyncio
async def test_client_passes_tenant_uri_to_mongodb_handler(
    monkeypatch: pytest.MonkeyPatch, recording_client: type[_RecordingClient]
) -> None:
    monkeypatch.setenv("MONGODB_MCP_URL", PLATFORM_URI)
    cfg = _mongo_cfg(TENANT_URI)

    result = await MCPClient(registry=AsyncMock())._call_tool_impl(
        cfg, cfg.server_id, "mongodb_count", {"collection": "c"}, TENANT
    )

    assert result.success, result.error
    (client,) = recording_client.instances
    assert "8.8.8.8" in client.dsn and "8.8.4.4" not in client.dsn


@pytest.mark.asyncio
async def test_client_blocks_private_mongodb_uri(
    recording_client: type[_RecordingClient],
) -> None:
    cfg = _mongo_cfg("mongodb://10.0.0.5:27017/db")

    result = await MCPClient(registry=AsyncMock())._call_tool_impl(
        cfg, cfg.server_id, "mongodb_count", {"collection": "c"}, TENANT
    )

    assert result.success is False
    assert recording_client.instances == []
