"""WS-12 [FAKE success] — Notion/GDrive/SharePoint are real, registered connectors.

Before this fix these three connectors existed but were never ``@register``-ed,
so the only way to reach them was a stub that reported success WITHOUT running
the pipeline. These tests pin that they are registered BaseConnectors whose
``get_delta`` yields real ``RawDocument`` objects routable through the pipeline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.base_connector import BaseConnector
from app.ingestion.connector_registry import get_connector, list_registered, load_all_connectors
from app.ingestion.connectors import gdrive_connector as gc
from app.ingestion.connectors import notion_connector as nc
from app.ingestion.connectors import sharepoint_connector as sc
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(connection: dict) -> SourceConfig:
    return SourceConfig(
        source_id="s1",
        tenant_id="t1",
        name="T",
        family=SourceFamily.DOCUMENT_STORE,
        source_type="test",
        collection_id="c1",
        connection_config=connection,
    )


@pytest.mark.parametrize("source_type", ["notion", "gdrive", "sharepoint"])
def test_connector_is_registered(source_type: str) -> None:
    load_all_connectors()
    assert source_type in list_registered()
    cls = get_connector(source_type)
    assert issubclass(cls, BaseConnector)


@pytest.mark.asyncio
async def test_notion_get_delta_yields_real_documents() -> None:
    load_all_connectors()
    conn = get_connector("notion")()
    pages = [
        {"id": "p1", "url": "https://notion.so/p1", "last_edited_time": "2026-01-02T00:00:00Z"}
    ]
    with (
        patch.object(nc.NotionConnector, "list_all_pages_in_workspace",
                     new=AsyncMock(return_value=pages)),
        patch.object(nc.NotionConnector, "fetch_page_content",
                     new=AsyncMock(return_value="Hello page body")),
    ):
        docs = [d async for d in conn.get_delta(_config({"api_key": "secret"}), None)]
    assert docs
    raw, cursor = docs[0]
    assert raw.content == b"Hello page body"
    assert raw.source_url == "https://notion.so/p1"
    assert cursor  # cursor advances


@pytest.mark.asyncio
async def test_gdrive_get_delta_yields_real_documents() -> None:
    load_all_connectors()
    conn = get_connector("gdrive")()
    files = [{"id": "f1", "name": "doc.txt", "mimeType": "text/plain",
              "modifiedTime": "2026-01-02T00:00:00Z"}]
    with (
        patch.object(gc.GDriveConnector, "list_files", new=MagicMock(return_value=files)),
        patch.object(gc.GDriveConnector, "download_file",
                     new=MagicMock(return_value="drive file text")),
    ):
        docs = [d async for d in conn.get_delta(
            _config({"folder_id": "F", "key_path": "/tmp/k.json"}), None)]
    assert docs
    raw, _ = docs[0]
    assert raw.content == b"drive file text"


@pytest.mark.asyncio
async def test_sharepoint_get_delta_yields_real_documents() -> None:
    load_all_connectors()
    conn = get_connector("sharepoint")()
    files = [{"id": "i1", "name": "f.txt", "file": {}, "webUrl": "https://sp/f",
              "lastModifiedDateTime": "2026-01-02T00:00:00Z"}]
    with (
        patch.object(sc.SharePointConnector, "list_all_files",
                     new=AsyncMock(return_value=files)),
        patch.object(sc.SharePointConnector, "download_file",
                     new=AsyncMock(return_value="sharepoint text")),
    ):
        docs = [d async for d in conn.get_delta(
            _config({"tenant_id": "az", "client_id": "c", "client_secret": "s",
                     "site_id": "site1"}), None)]
    assert docs
    raw, _ = docs[0]
    assert raw.content == b"sharepoint text"
