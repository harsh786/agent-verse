"""The one OCR document size limit (``OCR_MAX_UPLOAD_BYTES``).

Every OCR entry point enforces this same cap: ``/ocr/extract`` (multipart upload
or decoded base64), each ``/ocr/batch`` document, and the agent-callable
``extract_document`` tool. The tool used to carry its own hardcoded 10 MiB cap,
so a document between 10 MiB and the documented 25 MiB passed the API check and
was then refused with a 422 naming "10 MB".
"""

from __future__ import annotations

_MIB = 1024 * 1024
DEFAULT_OCR_MAX_UPLOAD_BYTES = 25 * _MIB


def ocr_max_upload_bytes() -> int:
    """The configured per-document OCR cap in bytes (``OCR_MAX_UPLOAD_BYTES``)."""
    try:
        from app.core.config import get_settings

        return int(get_settings().ocr_max_upload_bytes)
    except Exception:
        return DEFAULT_OCR_MAX_UPLOAD_BYTES


def describe_byte_limit(limit: int) -> str:
    """``"25 MiB (26214400 bytes)"``; a limit that is not whole MiB in bytes only
    (``"1000 bytes"``) — never a rounded-down ``"0 MiB"``."""
    if limit >= _MIB and limit % _MIB == 0:
        return f"{limit // _MIB} MiB ({limit} bytes)"
    if limit >= _MIB:
        return f"{limit / _MIB:.1f} MiB ({limit} bytes)"
    return f"{limit} bytes"


def ocr_limit_message(limit: int) -> str:
    """The 413 detail for a document over the OCR cap."""
    return (
        f"Document exceeds the OCR limit of {describe_byte_limit(limit)} "
        "per document (OCR_MAX_UPLOAD_BYTES)"
    )


class OcrDocumentTooLargeError(ValueError):
    """A document over ``OCR_MAX_UPLOAD_BYTES`` (the API answers 413).

    A ``ValueError`` so callers that already treat bad input as a ValueError
    keep working."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(ocr_limit_message(limit))
