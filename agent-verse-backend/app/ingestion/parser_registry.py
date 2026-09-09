"""ParserRegistry — maps ContentType to parser implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.ingestion.content_classifier import ContentType

if TYPE_CHECKING:
    from app.providers.base import LLMProvider


class TextParser:
    def parse(self, content: str, **kwargs: object) -> list[str]:
        """Split into paragraphs for semantic chunking."""
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        return paragraphs or [content]


class CodeParser:
    def parse(self, content: str, **kwargs: object) -> list[str]:
        """Split by function/class definitions."""
        import re

        blocks = re.split(r"(?m)^(?=def |class |function |const |let )", content)
        return [b.strip() for b in blocks if b.strip()] or [content]


class HTMLParser:
    def parse(self, content: str, **kwargs: object) -> list[str]:
        """Strip HTML tags and split into paragraphs."""
        import re

        text = re.sub(r"<[^>]+>", " ", content)
        text = re.sub(r"\s+", " ", text).strip()
        return [text] if text else [content]


class DOCXParser:
    def parse(self, content: str, **kwargs: object) -> list[str]:
        import re

        clean = re.sub(r"<[^>]+>", " ", content).strip()
        paragraphs = [p.strip() for p in clean.split("\n\n") if p.strip()]
        return paragraphs or [content]


class CSVParser:
    def parse(self, content: str, **kwargs: object) -> list[str]:
        return [content]


class PDFTextParser:
    def parse(self, content: str, **kwargs: object) -> list[str]:
        pages = content.split("\x0c")
        if len(pages) > 1:
            return [p.strip() for p in pages if p.strip()]
        return [p.strip() for p in content.split("\n\n") if p.strip()] or [content]


class AudioTranscriptParser:
    def parse(self, content: str, **kwargs: object) -> list[str]:
        return [content]


class VideoTranscriptParser:
    def parse(self, content: str, **kwargs: object) -> list[str]:
        return [content]


class JSONParser:
    def parse(self, content: str, **kwargs: object) -> list[str]:
        try:
            import json

            data = json.loads(content)
            if isinstance(data, list):
                return [json.dumps(item, indent=2) for item in data]
            return [json.dumps(data, indent=2)]
        except Exception:
            return [content]


class VisionParser:
    """Parser for image content types. Returns alt-text or a placeholder."""

    def parse(self, content: str, **kwargs: object) -> list[str]:
        """For image content, return the content as-is (alt-text / description)."""
        return [content] if content.strip() else []


# ── Bridge adapters: wrap bytes-based parsers into the str-based interface ────


class _ExcelBridge:
    def __init__(self, parser):
        self._p = parser

    def parse(self, content: str, **kwargs) -> list[str]:
        return [self._p.parse(content.encode("utf-8") if isinstance(content, str) else content)]


class _YAMLBridge:
    def __init__(self, parser):
        self._p = parser

    def parse(self, content: str, **kwargs) -> list[str]:
        return [self._p.parse(content)]


class _ParquetBridge:
    def __init__(self, parser):
        self._p = parser

    def parse(self, content: str, **kwargs) -> list[str]:
        raw = content.encode("latin-1") if isinstance(content, str) else content
        return [self._p.parse(raw)]


class _AvroBridge:
    def __init__(self, parser):
        self._p = parser

    def parse(self, content: str, **kwargs) -> list[str]:
        raw = content.encode("latin-1") if isinstance(content, str) else content
        return [self._p.parse(raw)]


class _LaTeXBridge:
    def __init__(self, parser):
        self._p = parser

    def parse(self, content: str, **kwargs) -> list[str]:
        result = self._p.parse(content)
        return [result] if result else [content]


class ParserRegistry:
    def __init__(self) -> None:
        from app.ingestion.parsers.avro_parser import AvroParser
        from app.ingestion.parsers.excel_parser import ExcelParser
        from app.ingestion.parsers.latex_parser import LaTeXParser
        from app.ingestion.parsers.parquet_parser import ParquetParser
        from app.ingestion.parsers.yaml_parser import YAMLParser

        self._parsers: dict[ContentType, object] = {
            ContentType.TEXT: TextParser(),
            ContentType.MARKDOWN: TextParser(),
            ContentType.CODE: CodeParser(),
            ContentType.HTML: HTMLParser(),
            ContentType.WEB_PAGE: HTMLParser(),
            ContentType.PDF: PDFTextParser(),
            ContentType.DOCX: DOCXParser(),
            ContentType.CSV: CSVParser(),
            ContentType.AUDIO: AudioTranscriptParser(),
            ContentType.VIDEO: VideoTranscriptParser(),
            ContentType.JSON: JSONParser(),
            ContentType.IMAGE: VisionParser(),
            # Extended types
            ContentType.EXCEL: _ExcelBridge(ExcelParser()),
            ContentType.YAML: _YAMLBridge(YAMLParser()),
            ContentType.PARQUET: _ParquetBridge(ParquetParser()),
            ContentType.AVRO: _AvroBridge(AvroParser()),
            ContentType.LATEX: _LaTeXBridge(LaTeXParser()),
            ContentType.NOTEBOOK: TextParser(),  # Notebook parser handles .ipynb via pipeline Stage 5  # noqa: E501
        }

    def get_parser(self, content_type: ContentType) -> TextParser:
        return self._parsers.get(content_type, TextParser())  # type: ignore[return-value]

    def parse(self, content: bytes, content_type: object) -> str:
        """Parse raw bytes into plain text. Used by IngestionPipeline Stage 5.

        Returns a single string of extracted text.
        Falls back to UTF-8 decode if parser raises.
        """
        from app.ingestion.content_classifier import ContentType

        # fixed: was ContentType.PLAIN_TEXT
        ct = content_type if isinstance(content_type, ContentType) else ContentType.TEXT
        parser = self._parsers.get(ct, TextParser())

        # Most parsers take str, but pipeline gives bytes
        try:
            # Try to decode first; parsers that need raw bytes have their own logic
            text_input = content.decode("utf-8", errors="replace")
            result = parser.parse(text_input)
            if isinstance(result, list):
                return "\n".join(result)
            return str(result)
        except Exception:
            # Ultimate fallback: raw decode
            return content.decode("utf-8", errors="replace")

    async def parse_bytes_async(
        self,
        content: bytes,
        content_type: object,
        *,
        filename: str = "",
        mime_type: str = "",
        ocr_engine: object | None = None,
        vision_provider: object | None = None,
    ) -> tuple[str, dict[str, object]]:
        """MIME-aware, byte-native parse bridging to the real parsers.

        Routes core binary/structured formats (PDF, DOCX, CSV, IMAGE, AUDIO)
        through the real ``app.ingestion.parsers.*`` implementations, awaiting
        the async ones. Returns ``(text, degradation_metadata)``.

        When an optional binary (fitz / pdfminer / python-docx) is missing, the
        degradation is surfaced in the metadata dict instead of returning raw
        container garbage (``PK``, ``<w:...>``).
        """
        from app.ingestion.content_classifier import ContentType

        ct = content_type if isinstance(content_type, ContentType) else ContentType.TEXT
        meta: dict[str, object] = {}
        name = filename or "document"

        try:
            if ct == ContentType.PDF:
                return await self._parse_pdf(content, name, meta, ocr_engine, vision_provider)
            if ct == ContentType.DOCX:
                return self._parse_docx(content, name, meta)
            if ct == ContentType.CSV:
                from app.ingestion.parsers.csv_parser import CSVParser as RealCSVParser

                text = RealCSVParser().parse(
                    content.decode("utf-8", errors="replace"), filename=name
                )
                return text, meta
            if ct == ContentType.IMAGE:
                return await self._parse_image(content, name, meta, ocr_engine, vision_provider)
            if ct == ContentType.AUDIO:
                return await self._parse_audio(content, name, mime_type, meta)
            if ct == ContentType.VIDEO:
                return await self._parse_video(content, name, meta)
        except Exception as exc:  # never leak binary garbage on unexpected failure
            meta["parse_error"] = str(exc)[:200]
            return "", meta

        # Generic str-based parsers (TEXT, MARKDOWN, CODE, HTML, JSON, EXCEL, …)
        return self.parse(content, ct), meta

    def _parse_docx(
        self, content: bytes, name: str, meta: dict[str, object]
    ) -> tuple[str, dict[str, object]]:
        import importlib.util

        if importlib.util.find_spec("docx") is None:
            # Without python-docx the .docx is a zip container — refuse to emit
            # its raw bytes (which would leak "PK"/"<w:" garbage).
            meta["docx_degraded"] = "python-docx not installed"
            return "", meta
        from app.ingestion.parsers.docx_parser import DOCXParser

        result = DOCXParser().parse_bytes(content, name)
        if result.error:
            meta["docx_error"] = result.error
        return "\n\n".join(result.paragraphs), meta

    async def _parse_pdf(
        self,
        content: bytes,
        name: str,
        meta: dict[str, object],
        ocr_engine: object | None,
        vision_provider: object | None,
    ) -> tuple[str, dict[str, object]]:
        import importlib.util

        from app.ingestion.parsers.pdf_parser import PDFParser

        has_fitz = importlib.util.find_spec("fitz") is not None
        has_pdfminer = importlib.util.find_spec("pdfminer") is not None
        if not has_fitz and not has_pdfminer:
            meta["pdf_degraded"] = "no fitz/pdfminer — text-layer extraction unavailable"

        result = PDFParser().parse_bytes(content, name)
        text = result.full_text

        # Scanned-PDF branch: no extractable text layer → OCR the rendered pages.
        if not text.strip() and ocr_engine is not None:
            ocr_res = await ocr_engine.extract(pdf_bytes=content, provider=vision_provider)  # type: ignore[attr-defined]
            ocr_text = getattr(ocr_res, "raw_text", "") or ""
            if ocr_text.strip():
                text = ocr_text
                meta["ocr_used"] = True
                engine_used = getattr(ocr_res, "engine_used", "")
                meta["ocr_engine"] = engine_used
                if engine_used and engine_used != "tesseract":
                    meta["ocr_fallback"] = engine_used
        return text, meta

    async def _parse_image(
        self,
        content: bytes,
        name: str,
        meta: dict[str, object],
        ocr_engine: object | None,
        vision_provider: object | None,
    ) -> tuple[str, dict[str, object]]:
        parts: list[str] = []

        # OCR text (Tesseract → LLM-vision fallback, honestly recorded).
        if ocr_engine is not None:
            ocr_res = await ocr_engine.extract(image_bytes=content, provider=vision_provider)  # type: ignore[attr-defined]
            ocr_text = getattr(ocr_res, "raw_text", "") or ""
            if ocr_text.strip():
                parts.append(ocr_text.strip())
            engine_used = getattr(ocr_res, "engine_used", "")
            if engine_used:
                meta["ocr_engine"] = engine_used
                if engine_used != "tesseract":
                    meta["ocr_fallback"] = engine_used

        # Vision description — only when a provider is configured (avoids
        # blind SDK calls when no credentials are available).
        if vision_provider is not None:
            from app.ingestion.parsers.vision_parser import VisionParser

            vres = await VisionParser(
                provider=cast("LLMProvider | None", vision_provider)
            ).parse_image_bytes(content, name)
            desc = vres.description or ""
            if desc.strip() and not desc.startswith("[Image:"):
                parts.append(desc.strip())
            if vres.error:
                meta["vision_error"] = vres.error

        if not parts:
            meta.setdefault("image_degraded", "no OCR/vision text extracted")
        return "\n\n".join(parts), meta

    async def _parse_audio(
        self,
        content: bytes,
        name: str,
        mime_type: str,
        meta: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        from app.ingestion.parsers.audio_parser import AudioParser

        result = await AudioParser().parse_bytes(
            content, name, mime_type or "audio/mpeg"
        )
        if result.error:
            meta["audio_degraded"] = result.error
            return "", meta
        return result.transcript, meta

    async def _parse_video(
        self,
        content: bytes,
        name: str,
        meta: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        """Transcribe video via the Whisper-backed VideoParser (extract audio → ASR)."""
        from app.ingestion.parsers.video_parser import VideoParser

        result = await VideoParser().parse_bytes(content, name)
        if result.error:
            meta["video_degraded"] = result.error
            return "", meta
        return result.transcript, meta
