"""DOCX document ingestor using python-docx."""
from __future__ import annotations
import io
from typing import Any
from app.observability.logging import get_logger

logger = get_logger(__name__)
_CHUNK_SIZE = 1000


class DocxIngestor:
    def extract_chunks(
        self, *, content: bytes, filename: str, source_url: str = ""
    ) -> list[dict[str, Any]]:
        try:
            from docx import Document
        except ImportError:
            logger.warning("python_docx_not_installed")
            return [{"content": f"[DOCX: {filename} — install python-docx]",
                     "source_url": source_url, "source_type": "docx",
                     "source_doc_id": filename, "page_number": None, "metadata": {}}]

        try:
            doc = Document(io.BytesIO(content))
            paragraphs = [p.text.strip() for p in doc.paragraphs if len(p.text.strip()) >= 20]
            full_text = "\n\n".join(paragraphs)
            chunks = []
            start = 0
            while start < len(full_text):
                chunk = full_text[start:start + _CHUNK_SIZE]
                if len(chunk.strip()) >= 30:
                    chunks.append({
                        "content": chunk, "source_url": source_url,
                        "source_type": "docx", "source_doc_id": filename,
                        "page_number": None, "metadata": {"filename": filename},
                    })
                start += 900
            return chunks
        except Exception as exc:
            logger.warning("docx_extract_failed", filename=filename, error=str(exc))
            return []
