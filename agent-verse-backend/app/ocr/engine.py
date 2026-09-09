"""OCR engine: Tesseract primary with LLM vision fallback."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from app.ocr.classifier import DocumentClassifier
from app.ocr.extractors import get_extractor
from app.ocr.models import DocumentType, OcrResult

_log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6

OcrFormat = Literal["image", "pdf", "office", "text", "unsupported"]

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
_OFFICE_EXTS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods", ".rtf"}
_TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".html", ".htm", ".xml"}


def detect_ocr_format(
    data: bytes, content_type: str | None, filename: str | None
) -> OcrFormat:
    """Classify an arbitrary input as an OCR input class.

    Precedence: content-type → filename extension → magic bytes. Office formats
    are zip containers (``PK\\x03\\x04``) so they are only recognised via the
    content-type/extension, never magic alone.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = Path(filename).suffix.lower() if filename else ""

    if ct.startswith("image/") or ext in _IMAGE_EXTS:
        return "image"
    if ct == "application/pdf" or ext == ".pdf":
        return "pdf"
    office_cts = ("officedocument", "msword", "ms-excel", "ms-powerpoint", "opendocument")
    if any(tok in ct for tok in office_cts) or ext in _OFFICE_EXTS:
        return "office"
    if ct.startswith("text/") or ext in _TEXT_EXTS:
        return "text"

    # Magic-byte fallback for callers that provide neither content-type nor name.
    if data[:5] == b"%PDF-":
        return "pdf"
    if (
        data[:8] == b"\x89PNG\r\n\x1a\n"
        or data[:3] == b"\xff\xd8\xff"  # JPEG
        or data[:6] in (b"GIF87a", b"GIF89a")
        or data[:2] == b"BM"  # BMP
        or data[:4] in (b"II*\x00", b"MM\x00*")  # TIFF
    ):
        return "image"
    return "unsupported"


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
        extractor = get_extractor(doc_type, provider=provider)

        # For LLM-structured extractor, call async method
        from app.ocr.extractors.general import LlmStructuredExtractor

        if isinstance(extractor, LlmStructuredExtractor):
            fields = await extractor.extract_async(raw_text)
        else:
            fields = extractor.extract(raw_text)

        return OcrResult(
            raw_text=raw_text,
            document_type=doc_type,
            fields=fields,
            engine_used=engine_used,  # type: ignore[arg-type]
            overall_confidence=overall_conf,
            page_count=len(pages),
        )

    async def extract_any(
        self,
        data: bytes,
        *,
        content_type: str | None = None,
        filename: str | None = None,
        provider: Any = None,
    ) -> OcrResult:
        """Universal entry point: OCR text out of ANY input format (WS-6).

        Routes by detected format — images and PDFs rasterize directly; office
        documents are converted to PDF via LibreOffice (``soffice``) when it is
        available, then rasterized. Formats that cannot be rasterized (native
        text, unknown binaries, office docs with no converter) return an honest
        degraded result recording *why*, never a silent empty drop. The detected
        ``source_format`` is always recorded on the result.
        """
        fmt = detect_ocr_format(data, content_type, filename)

        if fmt == "image":
            return self._annotate(
                await self.extract(image_bytes=data, provider=provider), source_format="image"
            )
        if fmt == "pdf":
            return self._annotate(
                await self.extract(pdf_bytes=data, provider=provider), source_format="pdf"
            )
        if fmt == "office":
            pdf_bytes = self._office_to_pdf(data, filename)
            if pdf_bytes is None:
                return self._degraded(
                    "office",
                    "office document conversion requires LibreOffice (soffice), "
                    "which is not available on this host",
                )
            return self._annotate(
                await self.extract(pdf_bytes=pdf_bytes, provider=provider), source_format="office"
            )
        if fmt == "text":
            return self._degraded(
                "text", "input is already machine-readable text; OCR is not applicable"
            )
        return self._degraded(
            "unsupported",
            f"unsupported format for OCR (content_type={content_type!r}, filename={filename!r})",
        )

    @staticmethod
    def _annotate(result: OcrResult, *, source_format: str) -> OcrResult:
        """Record the source format and flag a rasterization that yielded no pages."""
        result.source_format = source_format
        if result.page_count == 0 and not result.degraded:
            result.degraded = True
            result.degradation_reason = (
                f"could not rasterize {source_format} input to images "
                "(missing renderer, e.g. poppler/pdf2image or Pillow?)"
            )
        return result

    @staticmethod
    def _degraded(source_format: str, reason: str) -> OcrResult:
        _log.info("ocr_degraded format=%s reason=%s", source_format, reason)
        return OcrResult(
            raw_text="",
            document_type=DocumentType.GENERAL,
            overall_confidence=0.0,
            page_count=0,
            degraded=True,
            degradation_reason=reason,
            source_format=source_format,
        )

    @staticmethod
    def _office_to_pdf(data: bytes, filename: str | None) -> bytes | None:
        """Convert an office document to PDF bytes via LibreOffice headless.

        Returns ``None`` when no converter (``soffice``/``libreoffice``) is on
        PATH, or on any conversion failure — the caller degrades honestly.
        """
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            return None
        suffix = Path(filename).suffix if filename else ".docx"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / f"input{suffix}"
                src.write_bytes(data)
                subprocess.run(
                    [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                out = src.with_suffix(".pdf")
                if out.exists():
                    return out.read_bytes()
        except Exception as exc:
            _log.warning("office_to_pdf failed: %s", exc)
        return None

    def _to_images(
        self,
        *,
        image_bytes: bytes | None,
        pdf_bytes: bytes | None,
    ) -> list[Any]:
        """Convert input bytes to a list of PIL Images."""
        if image_bytes:
            try:
                from PIL import Image

                img: Any = Image.open(io.BytesIO(image_bytes))
                return [img]
            except Exception as exc:
                _log.warning("Failed to open image bytes: %s", exc)
                return []

        if pdf_bytes:
            try:
                from pdf2image import convert_from_bytes

                pages: list[Any] = convert_from_bytes(pdf_bytes)
                return pages
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
            import pytesseract

            img = self._preprocess_image(img)
            data = None
            for tess_lang in ("hin+eng", "eng"):
                try:
                    data = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda _lang=tess_lang: pytesseract.image_to_data(
                            img, lang=_lang, output_type=pytesseract.Output.DICT
                        ),
                    )
                    break
                except Exception as tess_exc:
                    if tess_lang == "eng":
                        raise
                    _log.debug(
                        "Tesseract lang '%s' unavailable, trying 'eng': %s",
                        tess_lang,
                        tess_exc,
                    )
                    data = None
                    continue
            assert data is not None
            confs = [c for c in data.get("conf", []) if isinstance(c, (int, float)) and c >= 0]
            avg_conf = (sum(confs) / len(confs) / 100.0) if confs else 0.0

            if avg_conf >= CONFIDENCE_THRESHOLD:
                text = " ".join(
                    t
                    for t, c in zip(data.get("text", []), data.get("conf", []), strict=False)
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
                        image_data=img_b64,
                    )
                ],
                model="gpt-4o",  # provider will use its own configured model
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

    @staticmethod
    def _preprocess_image(img: Any) -> Any:
        """Apply preprocessing to improve OCR accuracy.

        Converts to grayscale and applies adaptive thresholding (binarization).
        Requires Pillow >= 10.0.0 (already a dep).
        """
        try:
            from PIL import ImageFilter, ImageOps  # type: ignore[import-untyped]

            # Convert to grayscale
            if img.mode != "L":
                img = img.convert("L")
            # Apply slight sharpening to improve character edges
            img = img.filter(ImageFilter.SHARPEN)
            # Auto-contrast to improve binarization
            img = ImageOps.autocontrast(img, cutoff=2)
            return img
        except Exception:
            return img  # graceful fallback: return original if preprocessing fails
