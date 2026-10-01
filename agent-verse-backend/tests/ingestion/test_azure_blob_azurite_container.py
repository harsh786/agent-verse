"""BLOCKING-SDK: Azure Blob connector against a real Azurite (testcontainers).

azure-storage-blob calls now run on the SDK pool inside the egress-checked driver
scope; this proves validate and the incremental sync still work with the real
SDK. The connection string names an explicit ``BlobEndpoint`` (development
storage shortcuts are refused by the egress guard).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.ingestion.source_config import SourceConfig, SourceFamily

pytestmark = pytest.mark.integration

# Azurite's published, well-known development account key (not a secret).
_ACCOUNT = "devstoreaccount1"
_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="


@pytest.fixture(scope="module")
def connection_string() -> Iterator[str]:
    from azure.storage.blob import BlobServiceClient
    from testcontainers.azurite import AzuriteContainer

    container = AzuriteContainer().with_command(
        # The SDK may speak a newer storage API version than the image knows.
        "azurite-blob --blobHost 0.0.0.0 --skipApiVersionCheck --loose"
    )
    with container:
        port = container.get_exposed_port(10000)
        conn = (
            f"DefaultEndpointsProtocol=http;AccountName={_ACCOUNT};AccountKey={_KEY};"
            f"BlobEndpoint=http://localhost:{port}/{_ACCOUNT};"
        )
        service = BlobServiceClient.from_connection_string(conn)
        service.create_container("docs")
        service.get_blob_client("docs", "a.txt").upload_blob(b"hello from azurite")
        yield conn


@pytest.fixture(autouse=True)
def _allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _config(conn: str) -> SourceConfig:
    return SourceConfig(
        source_id="src-azurite",
        tenant_id="tenant-azurite",
        name="azure",
        family=SourceFamily.OBJECT_STORAGE,
        source_type="azure_blob",
        collection_id="col-1",
        connection_config={"connection_string": conn, "container": "docs"},
    )


async def test_validate_connection_with_the_real_sdk(connection_string: str) -> None:
    from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector

    health = await AzureBlobConnector().validate_connection(_config(connection_string))
    assert health.ok, health.error


async def test_get_delta_with_the_real_sdk(connection_string: str) -> None:
    from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector

    docs = [d async for d, _c in AzureBlobConnector().get_delta(_config(connection_string), None)]
    assert [d.content for d in docs] == [b"hello from azurite"]
