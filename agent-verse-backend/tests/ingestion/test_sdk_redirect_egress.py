"""NEO4J-EGRESS audit: SDKs that follow a server-named host are egress-checked too.

influxdb-client (urllib3 ``PoolManager``, redirects on by default) and the Azure
SDK (azure-core ``RedirectPolicy``) follow an HTTP redirect to whatever host the
server names. Only the configured endpoint was checked and pinned, so a tenant's
server answering ``302 Location: http://10.0.0.5/`` sent the platform there — the
same hole as a Neo4j routing table or Kafka's advertised brokers.

The fake SDKs below do what the real ones do on such a redirect: resolve the new
host with ``socket.getaddrinfo`` on the calling thread.
"""

from __future__ import annotations

import socket
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion import connector_egress as egress
from app.ingestion.connector_egress import ConnectorEgressBlockedError
from app.ingestion.source_config import SourceConfig, SourceFamily

PUBLIC = "93.184.216.34"
REDIRECT_TARGET = "10.0.0.5"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every name resolves public; IP literals resolve to themselves."""

    def _getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        name = host.decode() if isinstance(host, bytes) else str(host)
        ip = name if egress._is_ip_literal(name) else PUBLIC
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, int(port or 0)))]

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)


def _follow_redirect(*_a: Any, **_k: Any) -> Any:
    socket.getaddrinfo(REDIRECT_TARGET, 80, 0, socket.SOCK_STREAM)
    return []


def _config(source_type: str, cc: dict[str, Any]) -> SourceConfig:
    return SourceConfig(
        source_id=f"src-{source_type}",
        tenant_id="tenant-redirect",
        name=source_type,
        family=SourceFamily.WEB,
        source_type=source_type,
        collection_id="col-1",
        connection_config=cc,
    )


def _fake_influx() -> dict[str, ModuleType]:
    mod = ModuleType("influxdb_client")
    client = MagicMock()
    client.__enter__.return_value = client
    client.query_api.return_value.query.side_effect = _follow_redirect
    client.health.side_effect = _follow_redirect
    mod.InfluxDBClient = MagicMock(return_value=client)  # type: ignore[attr-defined]
    return {"influxdb_client": mod}


def _fake_azure() -> dict[str, ModuleType]:
    azure = ModuleType("azure")
    storage = ModuleType("azure.storage")
    blob = ModuleType("azure.storage.blob")
    service = MagicMock()
    service.get_container_client.return_value.list_blobs.side_effect = _follow_redirect
    blob.BlobServiceClient = MagicMock(return_value=service)  # type: ignore[attr-defined]
    storage.blob = blob  # type: ignore[attr-defined]
    azure.storage = storage  # type: ignore[attr-defined]
    return {"azure": azure, "azure.storage": storage, "azure.storage.blob": blob}


async def test_influxdb_redirect_to_an_internal_host_is_refused() -> None:
    from app.ingestion.connectors.influxdb_connector import InfluxDBConnector

    cfg = _config("influxdb", {"url": "https://influx.example.com", "bucket": "b"})
    with patch.dict(sys.modules, _fake_influx()), pytest.raises(ConnectorEgressBlockedError):
        [d async for d in InfluxDBConnector().get_delta(cfg, None)]


async def test_influxdb_validate_refuses_a_redirect_to_an_internal_host() -> None:
    from app.ingestion.connectors.influxdb_connector import InfluxDBConnector

    cfg = _config("influxdb", {"url": "https://influx.example.com"})
    with patch.dict(sys.modules, _fake_influx()):
        health = await InfluxDBConnector().validate_connection(cfg)
    assert health.ok is False
    assert "SSRF guard" in health.error


async def test_azure_blob_redirect_to_an_internal_host_is_refused() -> None:
    from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector

    cfg = _config("azure_blob", {"account_name": "acct123", "account_key": "k", "container": "c"})
    with patch.dict(sys.modules, _fake_azure()), pytest.raises(ConnectorEgressBlockedError):
        [d async for d in AzureBlobConnector().get_delta(cfg, None)]
