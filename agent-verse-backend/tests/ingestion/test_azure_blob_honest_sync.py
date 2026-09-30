"""KB-19: the Azure Blob connector never reports a silent empty sync as success,
keeps the SDK off the event loop, and reports + retries blobs it could not read.

* Without ``azure-storage-blob`` ``get_delta`` returned zero documents and the
  sync was reported successful.
* ``list_blobs`` / downloads ran synchronously on the event loop.
* Oversized or failed blobs were only logged and the cursor advanced past them,
  so they were never retried or reported.
"""

from __future__ import annotations

import datetime as dt
import sys
import threading
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.ingestion.base_connector import ConnectorUnavailableError
from app.ingestion.connectors.azure_blob_connector import AzureBlobConnector
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import (
    CONNECTOR_FAILURE_KEY,
    RawDocument,
    SourceConfig,
    SourceFamily,
)


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The egress pin is covered in test_connector_dns_pinning; skip real DNS here."""
    import contextlib

    from app.ingestion.connectors import azure_blob_connector as mod

    @contextlib.asynccontextmanager
    async def _no_pin(_config: Any) -> Any:
        yield None

    monkeypatch.setattr(mod, "_pinned_azure_egress", _no_pin)


def _config(**cc: Any) -> SourceConfig:
    base = {"account_name": "acct", "account_key": "k", "container": "docs"}
    base.update(cc)
    return SourceConfig(
        source_id="src-az",
        tenant_id="t-az",
        name="az",
        family=SourceFamily.WEB,
        source_type="azure_blob",
        connection_config=base,
        max_doc_size_bytes=100,
    )


def _blob(name: str, minute: int, size: int = 10) -> Any:
    return SimpleNamespace(
        name=name,
        size=size,
        last_modified=dt.datetime(2026, 9, 1, 12, minute, tzinfo=dt.UTC),
        content_settings=SimpleNamespace(content_type="text/plain"),
    )


class _Downloader:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def chunks(self) -> Any:
        yield self._data


class _Container:
    def __init__(self, blobs: list[Any], failing: set[str]) -> None:
        self._blobs = blobs
        self._failing = failing
        self.sdk_threads: list[int] = []

    def list_blobs(self, name_starts_with: Any = None) -> Any:
        self.sdk_threads.append(threading.get_ident())
        return iter(self._blobs)

    def download_blob(self, name: str, offset: int = 0, length: int = 0) -> Any:
        self.sdk_threads.append(threading.get_ident())
        if name in self._failing:
            raise ConnectionError("transient 503 from storage")
        return _Downloader(f"content of {name}".encode())


def _fake_sdk(container: _Container) -> dict[str, ModuleType]:
    azure = ModuleType("azure")
    storage = ModuleType("azure.storage")
    blob = ModuleType("azure.storage.blob")

    class BlobServiceClient:
        def __init__(self, *a: Any, **k: Any) -> None: ...

        @classmethod
        def from_connection_string(cls, *_a: Any) -> Any:
            return cls()

        def get_container_client(self, _name: str) -> Any:
            return container

    blob.BlobServiceClient = BlobServiceClient  # type: ignore[attr-defined]
    storage.blob = blob  # type: ignore[attr-defined]
    azure.storage = storage  # type: ignore[attr-defined]
    return {"azure": azure, "azure.storage": storage, "azure.storage.blob": blob}


async def _collect(
    container: _Container, cursor: str | None = None
) -> list[tuple[RawDocument, str]]:
    with patch.dict(sys.modules, _fake_sdk(container)):
        return [item async for item in AzureBlobConnector().get_delta(_config(), cursor)]


async def test_missing_sdk_fails_the_sync_instead_of_an_empty_success() -> None:
    with (
        patch.dict(sys.modules, {"azure.storage.blob": None}),
        pytest.raises(ConnectorUnavailableError),
    ):
        _ = [d async for d in AzureBlobConnector().get_delta(_config(), None)]


async def test_sdk_calls_run_off_the_event_loop() -> None:
    container = _Container([_blob("a.txt", 1)], set())
    loop_thread = threading.get_ident()
    docs = await _collect(container)
    assert len(docs) == 1
    assert container.sdk_threads and loop_thread not in container.sdk_threads


async def test_a_failed_blob_is_reported_and_the_cursor_stays_before_it() -> None:
    blobs = [_blob("a.txt", 1), _blob("b.txt", 2), _blob("c.txt", 3)]
    container = _Container(blobs, failing={"b.txt"})

    items = await _collect(container)

    reported = [d for d, _ in items if d.metadata.get(CONNECTOR_FAILURE_KEY)]
    assert [d.metadata["name"] for d in reported] == ["b.txt"]
    assert "transient 503" in reported[0].metadata[CONNECTOR_FAILURE_KEY]
    # The cursor never moves past the failed blob, so the next sync retries it.
    final_cursor = items[-1][1]
    assert final_cursor == blobs[0].last_modified.isoformat()

    # Next sync from that cursor: b.txt is fetched again and now succeeds.
    retry = await _collect(_Container(blobs, failing=set()), cursor=final_cursor)
    assert "b.txt" in {d.metadata["name"] for d, _ in retry}
    assert retry[-1][1] == blobs[2].last_modified.isoformat()


async def test_an_oversized_blob_is_reported_not_just_logged() -> None:
    container = _Container([_blob("huge.bin", 1, size=10_000), _blob("ok.txt", 2)], set())

    items = await _collect(container)

    reported = [d for d, _ in items if d.metadata.get(CONNECTOR_FAILURE_KEY)]
    assert [d.metadata["name"] for d in reported] == ["huge.bin"]
    assert "size cap" in reported[0].metadata[CONNECTOR_FAILURE_KEY]


async def test_the_pipeline_fails_a_reported_document_without_indexing_it() -> None:
    class _Store:
        chunks: list[Any] = []

        async def exists_by_hash(self, **_: Any) -> bool:
            return False

        async def ingest_chunks_async(self, chunks: list[Any], **_: Any) -> list[str]:
            self.chunks.extend(chunks)
            return []

    store = _Store()
    pipeline = IngestionPipeline(knowledge_store=store, embedder=object())
    doc = RawDocument(
        doc_id="x",
        source_id="src-az",
        tenant_id="t-az",
        content=b"",
        content_type="application/octet-stream",
        metadata={CONNECTOR_FAILURE_KEY: "download failed: boom"},
    )

    result = await pipeline.ingest(doc, _config())

    assert result.status == "failed"
    assert "download failed: boom" in result.error
    assert store.chunks == []
