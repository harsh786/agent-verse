"""Encrypted PDFs (P1a-8): permissions-only encryption is read, a password is refused.

Live P1a: a circular protected only by an owner password (an empty user
password: every viewer opens it without asking, as with many bank and
government PDFs) was refused 422 "encrypted PDFs are not supported". It is now
opened with the empty password; a PDF that needs a real password stays a 422
that says so.
"""

from __future__ import annotations

import pytest

from app.ingestion.document_text import DocumentParseError, extract_pdf_pages

_TEXT = "Pilotage above 300 metres LOA requires two pilots and the Heron tug pair."


def _pdf(user_password: str | None, method: str = "RC4") -> bytes:
    from fpdf import FPDF
    from fpdf.enums import EncryptionMethod

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, _TEXT, new_x="LMARGIN", new_y="NEXT")
    pdf.set_encryption(owner_password="owner-secret", user_password=user_password,
                       encryption_method=getattr(EncryptionMethod, method))
    return bytes(pdf.output())


@pytest.mark.parametrize("method", ["RC4", "AES_128", "AES_256"])
def test_owner_password_only_pdf_is_read(method: str) -> None:
    pages = extract_pdf_pages(_pdf("", method), filename="circular.pdf")
    assert _TEXT[:40] in pages[0]


def test_pdf_needing_a_password_is_refused_with_a_clear_reason() -> None:
    with pytest.raises(DocumentParseError, match="password"):
        extract_pdf_pages(_pdf("board-only"), filename="minutes.pdf")
