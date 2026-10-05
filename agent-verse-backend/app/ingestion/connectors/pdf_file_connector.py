"""PDF/DOCX file connectors — wrap existing parsers in BaseConnector."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorFetchError,
    ConnectorUnavailableError,
    describe_fetch_error,
    fetch_failure_document,
    is_retryable_fetch_error,
)
from app.ingestion.connector_egress import (
    ConnectorEgressBlockedError,
    assert_source_url,
    source_client,
)
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _doc_id(url: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, url).hex


class _UrlFileConnector(BaseConnector):
    """Fetches each configured URL as one file document.

    A URL that cannot be fetched yields a failure document (counted in the job,
    written to the DLQ) — it used to be logged and dropped, so a sync whose every
    download failed reported "completed" with 0 failures (USR-1).
    """

    source_type = ""
    _mime = "application/octet-stream"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        # These connectors fetch whatever URLs the tenant lists, so validation
        # has to actually check them. It previously returned ok=True without
        # looking at connection_config at all.
        urls = config.connection_config.get("urls", []) or []
        try:
            for url in urls:
                assert_source_url(str(url), context=f"{self.source_type}.validate", config=config)
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))
        return ConnectionHealth(
            ok=True, latency_ms=0.0, metadata={"type": "file", "urls": len(urls)}
        )

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        urls = config.connection_config.get("urls", []) or []
        if not urls:
            raise ConnectorFetchError(f"{self.source_type}: no urls configured")
        for url in urls:
            yield await self._fetch_one(config, str(url)), str(url)

    async def _fetch_one(self, config: SourceConfig, url: str) -> RawDocument:
        """The file at ``url``, or a failure document saying why it could not be read."""
        from app.ingestion.source_config import RawDocument

        try:
            assert_source_url(url, context=f"{self.source_type}.get_delta", config=config)
            async with source_client(timeout=60) as c:
                r = await c.get(url)
                r.raise_for_status()
                content_bytes = r.content
        except ConnectorUnavailableError:
            raise
        except Exception as exc:
            _log.warning("%s_fetch_error url=%s: %s", self.source_type, url, exc)
            return fetch_failure_document(
                config,
                doc_id=_doc_id(url),
                reason=describe_fetch_error(exc),
                retryable=not isinstance(exc, ConnectorEgressBlockedError)
                and is_retryable_fetch_error(exc),
                source_url=url,
                title=url.split("/")[-1],
            )
        return RawDocument(
            doc_id=_doc_id(url),
            source_id=config.source_id,
            tenant_id=config.tenant_id,
            content=content_bytes,
            content_type=self._mime,
            source_url=url,
            title=url.split("/")[-1],
        )


@register("pdf_file")
class PDFFileConnector(_UrlFileConnector):
    """Ingest PDF files provided as raw bytes (URL fetch or upload)."""

    source_type = "pdf_file"
    _mime = "application/pdf"


@register("docx_file")
class DOCXFileConnector(_UrlFileConnector):
    """Ingest DOCX files provided as raw bytes or URLs."""

    source_type = "docx_file"
    _mime = _DOCX_MIME
