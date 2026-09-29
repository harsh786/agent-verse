"""DOCX document ingestor using python-docx."""

from __future__ import annotations

from typing import Any

from app.ingestion.document_text import extract_docx_text

_CHUNK_SIZE = 1000


class DocxIngestor:
    """Raises ``DocumentParseError`` / ``ParserUnavailableError`` rather than
    returning a placeholder chunk or ``[]`` that was reported as ingested."""

    def extract_chunks(
        self, *, content: bytes, filename: str, source_url: str = ""
    ) -> list[dict[str, Any]]:
        text = extract_docx_text(content, filename=filename)
        paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) >= 20]
        full_text = "\n\n".join(paragraphs) or text.strip()
        chunks = []
        start = 0
        while start < len(full_text):
            chunk = full_text[start : start + _CHUNK_SIZE]
            if len(chunk.strip()) >= 30 or (start == 0 and chunk.strip()):
                chunks.append(
                    {
                        "content": chunk,
                        "source_url": source_url,
                        "source_type": "docx",
                        "source_doc_id": filename,
                        "page_number": None,
                        "metadata": {"filename": filename},
                    }
                )
            start += 900
        return chunks
