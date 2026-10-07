"""PDF rasterisation for OCR: one page at a time (OCR-PAR).

``pdf2image.convert_from_bytes(pdf)`` rendered EVERY page in one call and held
every bitmap at once (a 200-page scan at 300 dpi is ~1.7 GB of grayscale
pixels). These helpers render a single page (``first_page == last_page``) from a
file written once per document, so callers keep only the pages in flight in
memory. Both are blocking (poppler subprocesses): run them via
:func:`app.ocr.concurrency.run_ocr_work`, never on the event loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def pdf_page_count(path: str | Path) -> int:
    """Number of pages of the PDF at ``path`` (poppler ``pdfinfo``); raises."""
    from pdf2image import pdfinfo_from_path

    return int(pdfinfo_from_path(str(path))["Pages"])


def render_pdf_page_image(source: str | Path | bytes, page_number: int, *, dpi: int) -> Any:
    """Page ``page_number`` (1-based) as a grayscale PIL image; raises on failure.

    ``source`` is a PDF file path (preferred: written once per document) or the
    PDF bytes (written to a temporary file by pdf2image for this one page).
    """
    from pdf2image import convert_from_bytes, convert_from_path

    kwargs: dict[str, Any] = {
        "dpi": dpi,
        "first_page": page_number,
        "last_page": page_number,
        "grayscale": True,
    }
    if isinstance(source, bytes):
        images = convert_from_bytes(source, **kwargs)
    else:
        images = convert_from_path(str(source), **kwargs)
    if not images:
        raise ValueError(f"page {page_number} rendered no image")
    return images[0]


def pdf_structure_problem(data: bytes) -> tuple[str, str] | None:
    """Why the PDF cannot be opened at all, judged by pypdf (independent of
    poppler), else ``None``: ``("encrypted", reason)``, ``("no_pages", reason)``
    or ``("damaged", reason)`` (corrupt, truncated, not a PDF).

    Used to tell an input that can never be OCR'd (422) from a renderer that
    failed on a readable document (502). Pure Python but CPU-bound on large
    files: run it via :func:`app.ocr.concurrency.run_ocr_work`. Raises
    ``ImportError`` when pypdf is not installed.
    """
    import io

    import pypdf

    if not data.strip():
        return "damaged", "the file is empty"
    if b"%PDF-" not in data[:1024]:
        return "damaged", "the file is not a PDF (no %PDF- header)"
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                opened = bool(reader.decrypt(""))
            except Exception:
                opened = False
            if not opened:
                return (
                    "encrypted",
                    "the PDF is encrypted (password-protected) and cannot be opened "
                    "without its password",
                )
        pages = len(reader.pages)
    except Exception as exc:
        detail = " ".join(str(exc).split())[:160]
        return "damaged", f"the PDF is corrupt or truncated ({type(exc).__name__}: {detail})"
    if pages == 0:
        return "no_pages", "the PDF has no pages"
    return None


def describe_render_failure(exc: BaseException) -> str:
    """A client-safe description of a poppler / pdf2image failure."""
    name = type(exc).__name__
    if name == "PDFInfoNotInstalledError":
        return "the PDF renderer (poppler) is not installed on this host"
    if "Timeout" in name:
        return "the PDF renderer timed out"
    if name in {"PDFPageCountError", "PDFSyntaxError"}:
        # pdf2image puts poppler's stderr after its own first line.
        lines = [ln.strip() for ln in str(exc).splitlines()[1:] if ln.strip()]
        detail = "; ".join(dict.fromkeys(lines))[:200]
        return f"the PDF renderer could not open the document ({detail or name})"
    return f"the PDF renderer failed ({name})"
