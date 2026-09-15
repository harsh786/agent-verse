"""Phase 4 — multi-format document generation."""

from __future__ import annotations

import pytest

from app.chat.documents import generate_document, mime_for, supported_formats


def test_text_formats_return_utf8_bytes() -> None:
    assert generate_document("hello", "txt") == b"hello"
    assert generate_document("# Title", "md") == b"# Title"
    assert generate_document("a,b\n1,2", "csv") == b"a,b\n1,2"


def test_pdf_returns_valid_pdf_bytes() -> None:
    out = generate_document("Report line 1\nReport line 2", "pdf")
    assert isinstance(out, bytes) and out[:4] == b"%PDF" and len(out) > 200


def test_unsupported_format_raises() -> None:
    with pytest.raises(ValueError, match="unsupported document format"):
        generate_document("x", "docx")


def test_mime_and_supported() -> None:
    assert mime_for("pdf") == "application/pdf"
    assert mime_for("md") == "text/markdown"
    assert "pdf" in supported_formats() and "csv" in supported_formats()
