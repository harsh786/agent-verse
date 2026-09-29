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
    text = "\n".join(_docx_blocks(document))
    if not text.strip():
        raise DocumentParseError(f"{filename}: the document has no text")
    return text


def _docx_blocks(document: object) -> list[str]:
    """Paragraphs AND tables in document order.

    ``document.paragraphs`` skips tables entirely, so the figures a policy or a
    price list keeps in a table never reached the index. Each table row is
    emitted as ``header: value`` pairs so it stays searchable after chunking.
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body  # type: ignore[attr-defined]
    out: list[str] = []
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, document).text  # type: ignore[arg-type]
            if text.strip():
                out.append(text)
        elif tag == "tbl":
            table = Table(child, document)  # type: ignore[arg-type]
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            if not rows:
                continue
            header, *data_rows = rows
            if not data_rows:
                out.append(" | ".join(header))
                continue
            for row in data_rows:
                pairs = [
                    f"{header[i] if i < len(header) and header[i] else f'col{i + 1}'}: {v}"
                    for i, v in enumerate(row)
                    if v
                ]
                if pairs:
                    out.append("; ".join(pairs))
    return out


class UnsupportedDocumentError(ValueError):
    """The file type has no text extractor here (a 415 for an upload)."""


# Known binary office/e-book formats with no extractor installed.
_UNSUPPORTED_BINARY_EXTS = frozenset(
    {"doc", "xls", "ppt", "pptx", "odt", "ods", "odp", "rtf", "epub", "pages", "numbers", "key"}
)


def decode_text(data: bytes) -> str:
    """Decode an uploaded text file: UTF-8 (with or without BOM), UTF-16 with a
    BOM, else Windows-1252 — the common encodings of real-world exports."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def extract_upload_text(data: bytes, *, ext: str, filename: str) -> str:
    """The text of an uploaded file, by extension, or an exception — never garbage.

    Raises UnsupportedDocumentError (415), DocumentParseError (422: unreadable,
    empty or textless) or ParserUnavailableError (503: parser not installed).
    """
    ext = ext.lower().lstrip(".")
    if not data.strip():
        raise DocumentParseError(f"{filename}: the file is empty")
    if ext == "pdf":
        return "\n".join(extract_pdf_pages(data, filename=filename))
    if ext == "docx":
        return extract_docx_text(data, filename=filename)
    if ext in _UNSUPPORTED_BINARY_EXTS:
        raise UnsupportedDocumentError(
            f"{filename}: .{ext} files are not supported; convert to PDF, DOCX, XLSX or text"
        )
    if ext in {"xlsx", "xlsm"}:
        from app.ingestion.parsers.excel_parser import ExcelParser

        text = ExcelParser().parse(data, filename=filename)
        if not text.strip() or all(
            line.startswith("Sheet:") for line in text.splitlines() if line.strip()
        ):
            raise DocumentParseError(f"{filename}: not a readable workbook, or it has no data")
        return text
    if b"\x00" in data[:8192] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UnsupportedDocumentError(
            f"{filename}: unsupported binary file; upload PDF, DOCX, XLSX or text"
        )
    raw = decode_text(data)
    text = _extract_structured_text(raw, ext=ext, filename=filename)
    if not text.strip():
        raise DocumentParseError(f"{filename}: no extractable text")
    return text


def _extract_structured_text(raw: str, *, ext: str, filename: str) -> str:
    if ext in {"html", "htm", "xhtml"}:
        from app.ingestion.parsers.html_parser import HTMLParser

        return HTMLParser().parse(raw)
    if ext in {"csv", "tsv"}:
        from app.ingestion.parsers.csv_parser import CSVParser

        return CSVParser().parse(raw, filename=filename, delimiter="\t" if ext == "tsv" else "")
    if ext in {"json", "jsonl", "ndjson"}:
        from app.ingestion.parsers.json_parser import JSONParser

        return JSONParser().parse(raw, is_jsonl=ext != "json")
    if ext in {"yaml", "yml"}:
        from app.ingestion.parsers.yaml_parser import YAMLParser

        return YAMLParser().parse(raw, filename=filename)
    if ext == "ipynb":
        from app.ingestion.parsers.notebook_parser import NotebookParser

        return NotebookParser().parse(raw, filename=filename)
    if ext == "eml":
        from app.ingestion.parsers.email_parser import EmailParser

        return "\n\n".join(EmailParser().parse(raw))
    return raw
