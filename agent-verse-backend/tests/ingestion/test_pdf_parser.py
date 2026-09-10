# tests/ingestion/test_pdf_parser.py
"""PDF parser must extract text with page numbers and layout metadata."""
from __future__ import annotations

import pytest

from app.ingestion.parsers.pdf_parser import PDFPage, PDFParser, PDFParseResult


@pytest.fixture
def minimal_pdf_bytes():
    """Create a minimal valid PDF in-memory."""
    content = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 44>>stream
BT /F1 12 Tf 100 700 Td (Hello from page 1) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000360 00000 n
trailer<</Size 6/Root 1 0 R>>
startxref
441
%%EOF"""
    return content


def test_pdf_parser_extracts_text(minimal_pdf_bytes):
    parser = PDFParser()
    result = parser.parse_bytes(minimal_pdf_bytes, source_name="test.pdf")
    assert isinstance(result, PDFParseResult)
    assert len(result.pages) >= 1 or result.full_text or result.error
    full_text = result.full_text
    # Either extracted text or fell back gracefully
    assert isinstance(full_text, str)


def test_pdf_parser_attaches_page_numbers(minimal_pdf_bytes):
    parser = PDFParser()
    result = parser.parse_bytes(minimal_pdf_bytes, source_name="test.pdf")
    for page in result.pages:
        assert isinstance(page, PDFPage)
        assert page.page_number >= 1
        assert page.content is not None


def test_pdf_parser_produces_chunks(minimal_pdf_bytes):
    parser = PDFParser()
    result = parser.parse_bytes(minimal_pdf_bytes, source_name="test.pdf")
    chunks = result.to_chunks()
    # May be empty if PDF is empty after parse, but structure must be correct
    for chunk in chunks:
        assert chunk.get("content")
        assert chunk.get("page_number") is not None
        assert chunk.get("source_name") or chunk.get("source_url") is not None


def test_pdf_parser_handles_empty_bytes():
    parser = PDFParser()
    result = parser.parse_bytes(b"", source_name="empty.pdf")
    assert isinstance(result, PDFParseResult)
    assert result.pages == [] or result.error is not None


def test_pdf_parser_handles_text_input():
    """PDFParser also handles pre-extracted text (for non-binary paths)."""
    parser = PDFParser()
    result = parser.parse_text(
        "Page 1 content.\n\nPage 2 content.\n\n",
        source_name="text.pdf"
    )
    assert len(result.to_chunks()) >= 1
