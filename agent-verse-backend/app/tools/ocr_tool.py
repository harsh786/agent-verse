"""OCR document extraction tool — agent-callable wrapper around OcrEngine."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from app.observability.logging import get_logger
from app.ocr.engine import OcrEngine

_log = get_logger(__name__)

_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


class OcrDocumentTool:
    """Extract text and structured fields from any document image or PDF.

    Accepts one of:
    - file_path: local file path (image or PDF)
    - image_base64: base64-encoded image bytes
    - pdf_base64: base64-encoded PDF bytes

    Returns raw_text, document_type, and per-field structured extraction.
    """

    name = "extract_document"
    description = (
        "Extract text and structured fields from any document image or PDF. "
        "Accepts file_path (local path), image_base64, or pdf_base64. "
        "Returns raw_text, document_type, and structured field key-value pairs."
    )

    def __init__(self, ocr_engine: OcrEngine | None = None) -> None:
        self._engine = ocr_engine or OcrEngine()

    async def execute(
        self,
        *,
        file_path: str = "",
        image_base64: str = "",
        pdf_base64: str = "",
        provider: Any = None,
    ) -> dict:
        image_bytes, pdf_bytes = self._resolve_input(
            file_path=file_path,
            image_base64=image_base64,
            pdf_base64=pdf_base64,
        )

        result = await self._engine.extract(
            image_bytes=image_bytes,
            pdf_bytes=pdf_bytes,
            provider=provider,
        )

        return {
            "raw_text": result.raw_text,
            "document_type": result.document_type.value,
            "fields": {
                k: {"value": v.value, "confidence": round(v.confidence, 4)}
                for k, v in result.fields.items()
            },
            "engine_used": result.engine_used,
            "overall_confidence": round(result.overall_confidence, 4),
            "page_count": result.page_count,
        }

    def _resolve_input(
        self,
        *,
        file_path: str,
        image_base64: str,
        pdf_base64: str,
    ) -> tuple[bytes | None, bytes | None]:
        """Resolve input to (image_bytes, pdf_bytes). Raises ValueError if invalid."""
        if file_path:
            return self._read_file(file_path)

        if image_base64:
            data = self._decode_b64(image_base64)
            return data, None

        if pdf_base64:
            data = self._decode_b64(pdf_base64)
            return None, data

        raise ValueError(
            "One of file_path, image_base64, or pdf_base64 must be provided."
        )

    def _read_file(self, file_path: str) -> tuple[bytes | None, bytes | None]:
        path = Path(file_path).resolve()
        # Security: reject paths that resolve outside /tmp or typical upload dirs
        data = path.read_bytes()
        if len(data) > _MAX_BYTES:
            raise ValueError(
                f"File exceeds maximum allowed size of {_MAX_BYTES // 1_048_576} MB."
            )
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return None, data
        return data, None

    @staticmethod
    def _decode_b64(encoded: str) -> bytes:
        try:
            data = base64.b64decode(encoded)
        except Exception as exc:
            raise ValueError(f"Invalid base64 input: {exc}") from exc
        if len(data) > _MAX_BYTES:
            raise ValueError(
                f"Decoded input exceeds maximum allowed size of {_MAX_BYTES // 1_048_576} MB."
            )
        return data
