"""PDF/DOCX file connectors — wrap existing parsers in BaseConnector."""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, AsyncIterator

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("pdf_file")
class PDFFileConnector(BaseConnector):
    """Ingest PDF files provided as raw bytes (URL fetch or upload)."""

    source_type = "pdf_file"

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        return ConnectionHealth(ok=True, latency_ms=0.0, metadata={"type": "file"})

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument

        urls = config.connection_config.get("urls", [])
        if not urls:
            return

        for url in urls:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                    content_bytes = r.content
                raw = RawDocument(
                    doc_id=uuid.uuid5(uuid.NAMESPACE_URL, url).hex,
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    content=content_bytes,
                    content_type="application/pdf",
                    source_url=url,
                    title=url.split("/")[-1],
                )
                yield raw, url
            except Exception as exc:
                _log.warning("pdf_connector_fetch_error url=%s: %s", url, exc)


@register("docx_file")
class DOCXFileConnector(BaseConnector):
    """Ingest DOCX files provided as raw bytes or URLs."""

    source_type = "docx_file"

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        return ConnectionHealth(ok=True, latency_ms=0.0, metadata={"type": "file"})

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument

        urls = config.connection_config.get("urls", [])
        for url in urls:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                raw = RawDocument(
                    doc_id=uuid.uuid5(uuid.NAMESPACE_URL, url).hex,
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    content=r.content,
                    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    source_url=url,
                    title=url.split("/")[-1],
                )
                yield raw, url
            except Exception as exc:
                _log.warning("docx_connector_error url=%s: %s", url, exc)
