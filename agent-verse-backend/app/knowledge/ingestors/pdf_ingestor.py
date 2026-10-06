"""PDF document ingestor using pypdf (open-source, no cloud dependencies)."""

from __future__ import annotations

from typing import Any

from app.ingestion.document_text import extract_pdf_pages
from app.ingestion.quality_checks import is_meaningful_text

_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 100
_MAX_PAGES = 200


class PdfIngestor:
    """Extract text chunks from PDF files with page-level citation metadata.

    Raises ``DocumentParseError`` / ``ParserUnavailableError`` instead of
    returning a placeholder chunk or ``[]`` — both used to be "ingested" and
    reported as success.
    """

    def extract_chunks(
        self, *, content: bytes, filename: str, source_url: str = ""
    ) -> list[dict[str, Any]]:
        pages = extract_pdf_pages(content, filename=filename)
        total_pages = len(pages)
        chunks: list[dict[str, Any]] = []
        for page_num, raw in enumerate(pages[:_MAX_PAGES]):
            text = raw.strip()
            # A short page (a one-line notice) is indexed; only an empty or
            # noise page is skipped.
            if not is_meaningful_text(text):
                continue
            start = 0
            while start < len(text):
                chunk_text = text[start : start + _CHUNK_SIZE]
                if is_meaningful_text(chunk_text):
                    chunks.append(
                        {
                            "content": chunk_text,
                            "source_url": source_url,
                            "source_type": "pdf",
                            "source_doc_id": filename,
                            "page_number": page_num + 1,
                            "metadata": {
                                "filename": filename,
                                "page": page_num + 1,
                                "total_pages": total_pages,
                            },
                        }
                    )
                start += _CHUNK_SIZE - _CHUNK_OVERLAP
        return chunks
