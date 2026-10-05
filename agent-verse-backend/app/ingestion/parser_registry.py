"""ParserRegistry — maps ContentType to parser implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.ingestion.content_classifier import ContentType

if TYPE_CHECKING:
    from app.providers.base import LLMProvider


class DocumentParseError(ValueError):
    """A document in a binary / structured format could not be parsed.

    The document fails with this reason; its raw bytes are never indexed as
    text instead (which is what the UTF-8 fallback used to do).
    """


_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ZIP_MIMES = frozenset({"application/zip", "application/x-zip-compressed", "multipart/x-zip"})


def _ext_of(name: str) -> str:
    leaf = name.rsplit("/", 1)[-1]
    return leaf.rsplit(".", 1)[-1].lower() if "." in leaf else ""


def _a1_kind(ct: ContentType, name: str, mime_type: str) -> str | None:
    """Which upload-path (A1) extractor reads this document, if any."""
    ext = _ext_of(name)
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if ext == "pptx" or mime == _PPTX_MIME:
        return "pptx"
    if ext == "zip" or mime in _ZIP_MIMES:
        return "zip"
    if ct == ContentType.DOCX and (ext == "docx" or mime == _DOCX_MIME or not ext):
        return "docx"
    if ct in (ContentType.HTML, ContentType.WEB_PAGE):
        return "html"
    if ct == ContentType.PDF:
        return "pdf"
    return None


def _looks_binary(content: bytes) -> bool:
    """NUL bytes in the first 8 KiB (and no UTF-16 BOM): not a text document."""
    head = content[:8192]
    return b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff"))


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
            # Byte-native in parse_bytes_async (never parsed as plain text).
            ContentType.NOTEBOOK: TextParser(),
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
            # P1b-2: the formats file upload extracts with the A1 extractors are
            # read the same way here (connectors: S3/MinIO objects, Drive files…).
            a1 = await self._parse_a1(
                content, ct, name, mime_type, meta, ocr_engine, vision_provider
            )
            if a1 is not None:
                return a1, meta
            if ct in (ContentType.PARQUET, ContentType.AVRO):
                return self._parse_columnar(content, ct, name), meta
            if ct == ContentType.NOTEBOOK:
                from app.ingestion.parsers.notebook_parser import NotebookParser

                try:
                    text = NotebookParser().parse(
                        content.decode("utf-8", errors="strict"), filename=name, strict=True
                    )
                except (UnicodeDecodeError, ValueError) as exc:
                    raise DocumentParseError(str(exc)[:200]) from exc
                return text, meta
            if ct == ContentType.JSON:
                from app.ingestion.parsers.json_parser import JSONParser as RealJSONParser

                text, report = RealJSONParser().parse_with_report(
                    content.decode("utf-8", errors="replace"),
                    is_jsonl=name.lower().endswith(".jsonl"),
                )
                meta.update(report)
                return text, meta
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
            if ct == ContentType.EXCEL:
                from app.ingestion.parsers.excel_parser import ExcelParser

                # A truncated workbook is recorded in the ingestion metadata
                # instead of being indexed in part without a trace.
                text, report = ExcelParser().parse_with_report(content, filename=name)
                meta.update(report)
                return text, meta
            if ct == ContentType.IMAGE:
                return await self._parse_image(content, name, meta, ocr_engine, vision_provider)
            if ct == ContentType.AUDIO:
                return await self._parse_audio(content, name, mime_type, meta)
            if ct == ContentType.VIDEO:
                return await self._parse_video(content, name, meta)
        except DocumentParseError:
            raise
        except Exception as exc:  # never leak binary garbage on unexpected failure
            meta["parse_error"] = str(exc)[:200]
            return "", meta

        if _looks_binary(content):
            # Decoding it would index binary garbage (and Postgres refuses NUL
            # bytes): fail with the reason instead.
            raise DocumentParseError(
                f"{name}: unsupported binary content (no extractor for this file type)"
            )
        # Generic str-based parsers (TEXT, MARKDOWN, CODE, JSON, …)
        return self.parse(content, ct), meta

    async def _parse_a1(
        self,
        content: bytes,
        ct: ContentType,
        name: str,
        mime_type: str,
        meta: dict[str, object],
        ocr_engine: object | None,
        vision_provider: object | None,
    ) -> str | None:
        """Text via the upload path's extractors, or None for other formats.

        Raises :class:`DocumentParseError` with the extractor's reason for an
        unreadable document (corrupt, password-protected, zip bomb, no text).
        """
        from app.ingestion import document_text as dt

        kind = _a1_kind(ct, name, mime_type)
        if kind is None:
            return None
        try:
            if kind == "pptx":
                return dt.extract_pptx_text(content, filename=name)
            if kind == "zip":
                return await self._parse_archive(content, name, meta, ocr_engine, vision_provider)
            if kind == "docx":
                return dt.extract_docx_text(content, filename=name)
            if kind == "html":
                from app.ingestion.parsers.html_parser import HTMLParser as A1HTMLParser

                return A1HTMLParser().parse(dt.decode_text(content))
            # pdf
            return await self._parse_pdf_a1(content, name, meta, ocr_engine, vision_provider)
        except dt.ParserUnavailableError as exc:
            if isinstance(exc, dt.OcrUnavailableError):
                raise DocumentParseError(str(exc)[:300]) from exc
            return None  # optional library missing: the older parser path below
        except (dt.DocumentParseError, dt.UnsupportedDocumentError) as exc:
            raise DocumentParseError(str(exc)[:300]) from exc

    async def _parse_pdf_a1(
        self,
        content: bytes,
        name: str,
        meta: dict[str, object],
        ocr_engine: object | None,
        vision_provider: object | None,
    ) -> str:
        """Per-page text (pypdf, owner-password PDFs opened); pages without a text
        layer OCR'd one by one, as for uploads."""
        from app.ingestion import document_text as dt

        pages = dt.extract_pdf_pages(content, filename=name, allow_textless=True)
        textless = [i for i, page in enumerate(pages, start=1) if not page.strip()]
        if textless:
            ocrd = await dt.ocr_pdf_pages(
                content,
                filename=name,
                page_numbers=textless,
                vision_provider=vision_provider,
                ocr_engine=ocr_engine,
            )
            for number, (text, engine) in ocrd.items():
                pages[number - 1] = text
                if engine:
                    meta["ocr_engine"] = engine
            meta["ocr_used"] = True
            meta["ocr_pages"] = len(textless)
        text = "\n\n".join(p for p in pages if p.strip())
        if not text.strip():
            raise dt.DocumentParseError(f"{name}: the PDF has no extractable text")
        return text

    async def _parse_archive(
        self,
        content: bytes,
        name: str,
        meta: dict[str, object],
        ocr_engine: object | None,
        vision_provider: object | None,
    ) -> str:
        """Every member of a ZIP (nested archives expanded) under the upload limits,
        each as a section headed by its path; skipped members are reported."""
        import asyncio

        from app.core.config import get_settings
        from app.ingestion.archive import ArchiveLimits, ArchiveSkip, iter_archive
        from app.ingestion.document_text import IMAGE_UPLOAD_EXTS, extract_upload_text

        limits = ArchiveLimits.for_upload_limit(int(get_settings().knowledge_max_upload_bytes))
        members = iter_archive(content, filename=name, limits=limits)
        sections: list[str] = []
        skipped: list[str] = []
        while True:
            item = await asyncio.to_thread(next, members, None)  # inflate off the loop
            if item is None:
                break
            if isinstance(item, ArchiveSkip):
                skipped.append(f"{item.name}: {item.reason}")
                continue
            ext = item.ext or "txt"
            try:
                if ext in IMAGE_UPLOAD_EXTS:
                    text, _ = await self.parse_bytes_async(
                        item.data, ContentType.IMAGE, filename=item.path,
                        ocr_engine=ocr_engine, vision_provider=vision_provider,
                    )
                elif ext in {"pdf", "pptx"}:
                    text, _ = await self.parse_bytes_async(
                        item.data, ContentType.TEXT, filename=item.path,
                        ocr_engine=ocr_engine, vision_provider=vision_provider,
                    )
                else:
                    text = extract_upload_text(item.data, ext=ext, filename=item.path)
            except Exception as exc:  # this member only
                skipped.append(f"{item.path}: {str(exc)[:160]}")
                continue
            if text.strip():
                sections.append(f"{name}/{item.path}\n{text.strip()}")
            else:
                skipped.append(f"{item.path}: no extractable text")
        if skipped:
            meta["archive_members_skipped"] = skipped[:50]
        meta["archive_members_indexed"] = len(sections)
        if not sections:
            from app.ingestion.document_text import DocumentParseError as A1ParseError

            raise A1ParseError(
                f"{name}: the archive has no indexable files"
                + (f" ({'; '.join(skipped[:5])})" if skipped else "")
            )
        return "\n\n".join(sections)

    @staticmethod
    def _parse_columnar(content: bytes, ct: ContentType, name: str) -> str:
        """Parquet / Avro from the raw bytes (the old bridges got a lossy UTF-8
        round-trip, failed, and the raw ``PAR1...`` bytes were indexed)."""
        if ct == ContentType.PARQUET:
            from app.ingestion.parsers.parquet_parser import ParquetParser

            text = ParquetParser().parse(content, filename=name)
        else:
            from app.ingestion.parsers.avro_parser import AvroParser

            text = AvroParser().parse(content, filename=name)
        if not text.strip():
            raise DocumentParseError(
                f"{ct.value} file could not be parsed (invalid file, or the "
                f"{'pyarrow' if ct == ContentType.PARQUET else 'fastavro'} package is missing)"
            )
        return text

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
            # pypdf (core) still extracts the text layer; layout/tables need these.
            meta["pdf_parser"] = "pypdf"

        result = PDFParser().parse_bytes(content, name)
        text = result.full_text
        if result.error:
            meta["pdf_error"] = result.error

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
