"""SSRF: the Azure Blob connector dialled whatever ``connection_string`` said.

``BlobEndpoint=http://169.254.169.254/`` (or ``UseDevelopmentStorage=true`` →
127.0.0.1:10000, or an ``account_name`` of ``10.0.0.5/``) sent the SDK — with the
tenant's credentials — to an internal host and indexed what came back.

A fake ``azure.storage.blob`` module is installed so the guard (not an
ImportError) is what stops the connection. Targets are literal IPs (no DNS).
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.connector_egress import ConnectorEgressBlockedError
from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector, azure_blob_endpoints
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(cc: dict[str, Any]) -> SourceConfig:
    return SourceConfig(
        source_id="src-azure",
        tenant_id="tenant-ssrf",
        name="azure",
        family=SourceFamily.WEB,
        source_type="azure_blob",
        collection_id="col-1",
        connection_config={"container": "c", **cc},
    )


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real DNS: names under a metadata-looking suffix resolve internal."""
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(
        sg,
        "_resolve_host",
        lambda host: ["169.254.169.254"] if "169.254" in host else ["20.60.1.1"],
    )


def _fake_azure() ->tuple[dict[str, ModuleType], MagicMock]:
    azure = ModuleType("azure")
    storage = ModuleType("azure.storage")
    blob = ModuleType("azure.storage.blob")
    client_cls = MagicMock()
    blob.BlobServiceClient = client_cls  # type: ignore[attr-defined]
    storage.blob = blob  # type: ignore[attr-defined]
    azure.storage = storage  # type: ignore[attr-defined]
    return {"azure": azure, "azure.storage": storage, "azure.storage.blob": blob}, client_cls


_BAD: list[dict[str, Any]] = [
    {"connection_string": "BlobEndpoint=http://169.254.169.254/;SharedAccessSignature=sv=1"},
    {"connection_string": "blobendpoint=https://10.0.0.5:10000/devstore;AccountName=a"},
    {"connection_string": "UseDevelopmentStorage=true"},
    {"connection_string": "UseDevelopmentStorage=true;DevelopmentStorageProxyUri=http://8.8.8.8"},
    {
        "connection_string": (
            "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=k;"
            "BlobSecondaryEndpoint=http://127.0.0.1:80/"
        )
    },
    {
        "connection_string": (
            "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=k;"
            "EndpointSuffix=169.254.169.254"
        )
    },
    {"connection_string": "AccountName=169.254.169.254/;AccountKey=k"},
    {"connection_string": "AccountKey=k"},  # no endpoint derivable → blocked
    {"account_name": "10.0.0.5/", "account_key": "k"},
    {"account_name": "x@127.0.0.1#", "account_key": "k"},
    {"account_name": "", "account_key": "k"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("cc", _BAD)
async def test_validate_connection_blocks_internal_endpoint(cc: dict[str, Any]) -> None:
    mods, client_cls = _fake_azure()
    with patch.dict(sys.modules, mods):
        health = await AzureBlobConnector().validate_connection(_config(cc))
    assert health.ok is False
    client_cls.assert_not_called()
    client_cls.from_connection_string.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cc", _BAD)
async def test_get_delta_blocks_internal_endpoint(cc: dict[str, Any]) -> None:
    mods, client_cls = _fake_azure()
    with patch.dict(sys.modules, mods), pytest.raises(ConnectorEgressBlockedError):
        _ = [d async for d in AzureBlobConnector().get_delta(_config(cc), None)]
    client_cls.assert_not_called()
    client_cls.from_connection_string.assert_not_called()


def test_public_endpoints_are_derived_and_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["20.60.1.1"])
    cs = "DefaultEndpointsProtocol=https;AccountName=acct1;AccountKey=k;EndpointSuffix=core.windows.net"
    assert azure_blob_endpoints({"connection_string": cs}) == [
        "https://acct1.blob.core.windows.net"
    ]
    assert azure_blob_endpoints({"account_name": "acct1", "account_key": "k"}) == [
        "https://acct1.blob.core.windows.net"
    ]


@pytest.mark.asyncio
async def test_public_connection_string_reaches_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.net.ssrf_guard as sg

    monkeypatch.setattr(sg, "_resolve_host", lambda host: ["20.60.1.1"])
    mods, client_cls = _fake_azure()
    cs = "BlobEndpoint=https://acct1.blob.core.windows.net/;SharedAccessSignature=sv=1"
    with patch.dict(sys.modules, mods):
        health = await AzureBlobConnector().validate_connection(_config({"connection_string": cs}))
    client_cls.from_connection_string.assert_called_once_with(cs)
    assert health.ok is True


# ── DEC-SSRF: development storage follows ALLOW_PRIVATE_NETWORK_ACCESS ───────
# The Azurite emulator (UseDevelopmentStorage=true -> 127.0.0.1:10000) used to be
# refused outright. With the flag on it is a private endpoint like any other: it
# goes through the egress guard and is pinned; metadata stays blocked.


@pytest.mark.asyncio
async def test_development_storage_reaches_sdk_with_private_access_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    cs = "UseDevelopmentStorage=true"
    assert azure_blob_endpoints({"connection_string": cs}) == [
        "http://127.0.0.1:10000/devstoreaccount1"
    ]
    mods, client_cls = _fake_azure()
    with patch.dict(sys.modules, mods):
        health = await AzureBlobConnector().validate_connection(_config({"connection_string": cs}))
    client_cls.from_connection_string.assert_called_once_with(cs)
    assert health.ok is True


def test_private_blob_endpoint_allowed_with_private_access_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingestion.connector_egress import pin_source_urls_sync

    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    cs = "BlobEndpoint=http://192.168.63.104:10000/devstoreaccount1;AccountName=a"
    endpoints = azure_blob_endpoints({"connection_string": cs})
    with pin_source_urls_sync(endpoints, context="azure_blob"):
        pass


@pytest.mark.parametrize(
    "cs",
    [
        "UseDevelopmentStorage=true;DevelopmentStorageProxyUri=http://169.254.169.254",
        "BlobEndpoint=http://169.254.169.254/;SharedAccessSignature=sv=1",
    ],
)
def test_metadata_stays_blocked_with_private_access_on(
    monkeypatch: pytest.MonkeyPatch, cs: str
) -> None:
    from app.ingestion.connector_egress import pin_source_urls_sync

    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    endpoints = azure_blob_endpoints({"connection_string": cs})
    with (
        pytest.raises(ConnectorEgressBlockedError),
        pin_source_urls_sync(endpoints, context="azure_blob"),
    ):
        pass


def test_development_storage_refused_with_private_access_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
    with pytest.raises(ConnectorEgressBlockedError, match="development storage"):
        azure_blob_endpoints({"connection_string": "UseDevelopmentStorage=true"})
