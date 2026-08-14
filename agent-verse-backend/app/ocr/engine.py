"""OCR engine: Tesseract primary with LLM vision fallback."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import Any

from app.ocr.classifier import DocumentClassifier
from app.ocr.extractors import get_extractor
from app.ocr.models import DocumentType, OcrResult

_log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6


class OcrEngine:
    """Extract text and structured fields from images and PDFs.

    Primary: Tesseract (local, free, offline).
    Fallback: LLM vision via existing app.providers when Tesseract is
    unavailable or returns low confidence.
    """

    def __init__(self) -> None:
        self._classifier = DocumentClassifier()

    async def extract(
        self,
        *,
        image_bytes: bytes | None = None,
        pdf_bytes: bytes | None = None,
        provider: Any = None,
    ) -> OcrResult:
        """Extract text and structured fields from an image or PDF."""
        pages = self._to_images(image_bytes=image_bytes, pdf_bytes=pdf_bytes)
        if not pages:
            return OcrResult(
                raw_text="",
                document_type=DocumentType.GENERAL,
                overall_confidence=0.0,
                page_count=0,
            )

        raw_texts: list[tuple[str, float, str]] = []
        for page_img in pages:
            text, conf, engine_name = await self._ocr_page(page_img, provider=provider)
            raw_texts.append((text, conf, engine_name))

        raw_text = "\n\n".join(t for t, _, _ in raw_texts)
        overall_conf = sum(c for _, c, _ in raw_texts) / len(raw_texts)
        engine_used = raw_texts[0][2] if raw_texts else "tesseract"

        doc_type = self._classifier.classify(raw_text)
        extractor = get_extractor(doc_type)
        fields = extractor.extract(raw_text)

        return OcrResult(
            raw_text=raw_text,
            document_type=doc_type,
            fields=fields,
            engine_used=engine_used,  # type: ignore[arg-type]
            overall_confidence=overall_conf,
            page_count=len(pages),
        )

    def _to_images(
        self,
        *,
        image_bytes: bytes | None,
        pdf_bytes: bytes | None,
    ) -> list[Any]:
        """Convert input bytes to a list of PIL Images."""
        if image_bytes:
            try:
                from PIL import Image  # type: ignore[import-untyped]

                return [Image.open(io.BytesIO(image_bytes))]
            except Exception as exc:
                _log.warning("Failed to open image bytes: %s", exc)
                return []

        if pdf_bytes:
            try:
                from pdf2image import convert_from_bytes  # type: ignore[import-untyped]

                return convert_from_bytes(pdf_bytes)
            except ImportError:
                _log.debug("pdf2image not installed; skipping PDF rendering")
                return []
            except Exception as exc:
                _log.warning("Failed to convert PDF: %s", exc)
                return []

        return []

    async def _ocr_page(
        self,
        img: Any,
        *,
        provider: Any = None,
    ) -> tuple[str, float, str]:
        """Run OCR on a single page image. Returns (text, confidence, engine_name)."""
        try:
            import pytesseract  # type: ignore[import-untyped]

            data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: pytesseract.image_to_data(
                    img, output_type=pytesseract.Output.DICT
                ),
            )
            confs = [c for c in data.get("conf", []) if isinstance(c, (int, float)) and c >= 0]
            avg_conf = (sum(confs) / len(confs) / 100.0) if confs else 0.0

            if avg_conf >= CONFIDENCE_THRESHOLD:
                text = " ".join(
                    t
                    for t, c in zip(data.get("text", []), data.get("conf", []))
                    if isinstance(c, (int, float)) and c >= 0 and t.strip()
                )
                return text, avg_conf, "tesseract"

            _log.debug("Tesseract confidence %.2f below threshold; using LLM vision", avg_conf)
        except ImportError:
            _log.debug("pytesseract not installed; using LLM vision fallback")
        except Exception as exc:
            _log.warning("Tesseract failed: %s; using LLM vision fallback", exc)

        return await self._llm_vision_ocr(img, provider=provider)

    async def _llm_vision_ocr(
        self,
        img: Any,
        *,
        provider: Any = None,
    ) -> tuple[str, float, str]:
        """Use LLM vision to extract text from an image."""
        if provider is None:
            _log.warning("No provider for LLM vision OCR; returning empty text")
            return "", 0.0, "llm_vision"

        img_b64 = self._image_to_base64(img)
        try:
            from app.providers.base import CompletionRequest, Message

            req = CompletionRequest(
                messages=[
                    Message(
                        role="user",
                        content=(
                            "Extract all text from this image. "
                            "Return only the extracted text, no commentary."
                        ),
                    )
                ],
                image_base64=img_b64,
            )
            response = await provider.complete(req)
            return response.content, 0.85, "llm_vision"
        except Exception as exc:
            _log.warning("LLM vision OCR failed: %s", exc)
            return "", 0.0, "llm_vision"

    @staticmethod
    def _image_to_base64(img: Any) -> str:
        buf = io.BytesIO()
        try:
            img.save(buf, format="PNG")
        except Exception:
            return ""
        return base64.b64encode(buf.getvalue()).decode()
