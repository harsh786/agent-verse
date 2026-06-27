"""PDF document ingestor using pypdf (open-source, no cloud dependencies)."""
from __future__ import annotations
import io
from typing import Any
from app.observability.logging import get_logger

logger = get_logger(__name__)
_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 100
_MAX_PAGES = 200


class PdfIngestor:
    """Extract text chunks from PDF files with page-level citation metadata."""

    def extract_chunks(
        self, *, content: bytes, filename: str, source_url: str = ""
    ) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        try:
            from pypdf import PdfReader
        except ImportError:
            logger.warning("pypdf_not_installed", hint="pip install pypdf")
            return [{
                "content": f"[PDF: {filename} — install pypdf for text extraction]",
                "source_url": source_url, "source_type": "pdf",
                "source_doc_id": filename, "page_number": None,
                "metadata": {"filename": filename, "error": "pypdf_not_installed"},
            }]

        try:
            reader = PdfReader(io.BytesIO(content))
            total_pages = len(reader.pages)
            for page_num, page in enumerate(reader.pages[:_MAX_PAGES]):
                text = (page.extract_text() or "").strip()
                if len(text) < 30:
                    continue
                # Sliding window chunking
                start = 0
                while start < len(text):
                    chunk_text = text[start:start + _CHUNK_SIZE]
                    if len(chunk_text.strip()) >= 30:
                        chunks.append({
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
                        })
                    start += _CHUNK_SIZE - _CHUNK_OVERLAP
        except Exception as exc:
            logger.warning("pdf_extract_failed", filename=filename, error=str(exc))

        return chunks
