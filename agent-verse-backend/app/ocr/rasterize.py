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
