"""Text extraction for binary documents (PDF, DOCX) — one fail-closed path.

Every upload path used to "degrade" when a parser was missing or failed: the
raw bytes were decoded as UTF-8 (so a PDF was indexed as ``%PDF-1.3 … endobj``)
or a placeholder chunk ("install pypdf") was embedded, and the upload reported
success. A knowledge base full of PDF syntax retrieves nothing useful and looks
healthy. These helpers either return the document's real text or raise.
"""

from __future__ import annotations

import io


class DocumentParseError(ValueError):
    """The document could not be parsed, or contains no extractable text."""


class ParserUnavailableError(RuntimeError):
    """The parser library for this format is not installed on this host."""


def extract_pdf_pages(data: bytes, *, filename: str = "document.pdf") -> list[str]:
    """Return the text of each page (``""`` for pages without a text layer).

    Raises DocumentParseError for a corrupt/encrypted PDF or one with no text
    at all (e.g. a scan, which needs OCR), ParserUnavailableError without pypdf.
    """
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - pypdf is a core dependency
        raise ParserUnavailableError("PDF parsing requires pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentParseError(f"{filename}: encrypted PDFs are not supported")
        pages = [page.extract_text() or "" for page in reader.pages]
    except DocumentParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise DocumentParseError(f"{filename}: not a readable PDF ({exc})") from exc
    if not any(p.strip() for p in pages):
        raise DocumentParseError(
            f"{filename}: the PDF has no extractable text (scanned images need OCR)"
        )
    return pages


def extract_docx_text(data: bytes, *, filename: str = "document.docx") -> str:
    """Return the paragraph text of a .docx; raise instead of guessing."""
    try:
        import docx
    except ImportError as exc:  # pragma: no cover - python-docx is a core dependency
        raise ParserUnavailableError("DOCX parsing requires python-docx") from exc
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several unrelated types
        raise DocumentParseError(f"{filename}: not a readable .docx ({exc})") from exc
    text = "\n".join(p.text for p in document.paragraphs)
    if not text.strip():
        raise DocumentParseError(f"{filename}: the document has no text")
    return text
