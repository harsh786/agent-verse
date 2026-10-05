"""USR-4: connectors that report a failed fetch can fetch the item again.

A failure document carries a replay reference; the DLQ retry asks the connector
to fetch that one item again (``replay_event``) instead of replaying the empty
failure document, which can never succeed.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ingestion.base_connector import ConnectorFetchError
from app.ingestion.source_config import (
    CONNECTOR_FAILURE_KEY,
    CONNECTOR_REPLAY_KEY,
    SourceConfig,
    SourceFamily,
)

pytestmark = pytest.mark.asyncio


def _config(source_type: str, cc: dict[str, Any]) -> SourceConfig:
    return SourceConfig(
        source_id="src-r", tenant_id="t-r", name="n", family=SourceFamily.WEB,
        source_type=source_type, connection_config=cc, collection_id="col",
    )


def _send(status: int, body: bytes = b"") -> Any:
    async def send(self: httpx.AsyncClient, request: httpx.Request, **_: Any) -> httpx.Response:
        return httpx.Response(status, content=body, request=request)

    return send


async def _replay(connector: Any, config: SourceConfig, reference: dict[str, Any]) -> list[Any]:
    return [d async for d in connector.replay_event(config, reference)]


async def test_pdf_failure_carries_a_url_replay_that_refetches_it() -> None:
    from app.ingestion.connectors.pdf_file_connector import PDFFileConnector

    url = "https://example.com/a.pdf"
    config = _config("pdf_file", {"urls": [url]})
    with patch.object(httpx.AsyncClient, "send", _send(503)):
        (failed, _cursor) = [d async for d in PDFFileConnector().get_delta(config, None)][0]
    reference = failed.metadata[CONNECTOR_REPLAY_KEY]
    with patch.object(httpx.AsyncClient, "send", _send(200, b"%PDF-1.4")):
        (doc,) = await _replay(PDFFileConnector(), config, reference)
    assert doc.content == b"%PDF-1.4" and doc.doc_id == failed.doc_id


async def test_url_replay_refuses_a_url_the_source_no_longer_lists() -> None:
    from app.ingestion.connectors.pdf_file_connector import PDFFileConnector

    config = _config("pdf_file", {"urls": ["https://example.com/other.pdf"]})
    with pytest.raises(ConnectorFetchError, match="no longer configured"):
        await _replay(
            PDFFileConnector(), config, {"kind": "url", "url": "https://example.com/a.pdf"}
        )


async def test_web_page_replay_fetches_the_page_or_reports_it_again() -> None:
    from app.ingestion.connectors.web_crawl_connector import WebCrawlConnector

    url = "https://example.com/docs"
    config = _config("web_crawl", {"seed_urls": [url], "crawl_delay_seconds": 0})
    with patch.object(httpx.AsyncClient, "send", _send(502)):
        docs = [d async for d, _ in WebCrawlConnector().get_delta(config, None)]
    reference = docs[0].metadata[CONNECTOR_REPLAY_KEY]
    assert reference == {"kind": "web_page", "url": url}

    with patch.object(httpx.AsyncClient, "send", _send(503)):
        (again,) = await _replay(WebCrawlConnector(), config, reference)
    assert CONNECTOR_FAILURE_KEY in again.metadata

    html = b"<html><title>Docs</title><body>" + b"Useful words. " * 20 + b"</body></html>"
    with patch.object(httpx.AsyncClient, "send", _send(200, html)):
        (page,) = await _replay(WebCrawlConnector(), config, reference)
    assert page.title == "Docs" and page.doc_id == docs[0].doc_id
    assert CONNECTOR_FAILURE_KEY not in page.metadata


async def test_gcs_blob_replay_downloads_the_blob_again() -> None:
    from app.ingestion.connectors import gcs_connector

    blob = MagicMock(content_type="text/plain")
    blob.download_as_bytes.return_value = b"blob body"
    client = MagicMock()
    client.bucket.return_value.blob.return_value = blob
    fake = ModuleType("google.cloud.storage")
    import google.cloud

    config = _config("gcs", {"bucket": "b1"})
    with (
        patch.dict(sys.modules, {"google.cloud.storage": fake}),
        patch.object(google.cloud, "storage", fake, create=True),
        patch.object(gcs_connector, "_make_client", return_value=client),
    ):
        (doc,) = await _replay(
            gcs_connector.GCSConnector(), config, {"kind": "gcs_blob", "bucket": "b1", "name": "x"}
        )
        with pytest.raises(ConnectorFetchError):
            await _replay(
                gcs_connector.GCSConnector(), config,
                {"kind": "gcs_blob", "bucket": "someone-elses", "name": "x"},
            )
    assert doc.content == b"blob body" and doc.source_url == "gs://b1/x"


async def test_sharepoint_file_replay_downloads_the_file_again() -> None:
    from app.ingestion.connectors import sharepoint_connector as sp

    config = _config("sharepoint", {"tenant_id": "t", "client_id": "c", "client_secret": "s"})
    with (
        patch.object(sp.SharePointConnector, "get_file_metadata",
                     AsyncMock(return_value={"name": "f.txt", "webUrl": "https://sp/f"})),
        patch.object(sp.SharePointConnector, "download_file", AsyncMock(return_value="hello")),
    ):
        (doc,) = await _replay(
            sp.SharePointSourceConnector(), config,
            {"kind": "sharepoint_file", "site_id": "s1", "item_id": "i1"},
        )
    assert doc.content == b"hello" and doc.title == "f.txt"
