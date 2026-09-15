"""Multi-format document generation for chat (Phase 4, output side).

Turns text/markdown/CSV/JSON content into downloadable bytes, plus PDF via fpdf2
(already used by app/rpa/report.py). Used by a chat document-generation skill/
artifact so "make a PDF of this" produces a real file.
"""

from __future__ import annotations

_TEXT_FORMATS = {"txt", "text", "md", "markdown", "csv", "json"}

MIME_TYPES: dict[str, str] = {
    "txt": "text/plain",
    "text": "text/plain",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
    "pdf": "application/pdf",
}


def supported_formats() -> frozenset[str]:
    return frozenset(_TEXT_FORMATS | {"pdf"})


def mime_for(fmt: str) -> str:
    return MIME_TYPES.get(fmt.lower(), "application/octet-stream")


def generate_document(content: str, fmt: str = "txt") -> bytes:
    """Render *content* to bytes in *fmt*. Raises ValueError for unknown formats."""
    f = fmt.lower()
    if f in _TEXT_FORMATS:
        return content.encode("utf-8")
    if f == "pdf":
        return _render_pdf(content)
    raise ValueError(f"unsupported document format: {fmt}")


def _render_pdf(content: str) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in content.splitlines() or [""]:
        # latin-1 fallback keeps fpdf's core fonts happy on odd characters;
        # empty lines render as a blank line rather than raising.
        safe = line.encode("latin-1", "replace").decode("latin-1") or " "
        pdf.multi_cell(0, 6, safe, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())
