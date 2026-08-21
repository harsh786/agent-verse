"""ParserRegistry — maps ContentType to parser implementation."""

from __future__ import annotations

from app.ingestion.content_classifier import ContentType


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
            ContentType.NOTEBOOK: TextParser(),  # Notebook parser handles .ipynb via pipeline Stage 5
        }

    def get_parser(self, content_type: ContentType) -> TextParser:
        return self._parsers.get(content_type, TextParser())  # type: ignore[return-value]

    def parse(self, content: bytes, content_type: object) -> str:
        """Parse raw bytes into plain text. Used by IngestionPipeline Stage 5.

        Returns a single string of extracted text.
        Falls back to UTF-8 decode if parser raises.
        """
        from app.ingestion.content_classifier import ContentType as CT

        ct = content_type if isinstance(content_type, CT) else CT.TEXT  # fixed: was CT.PLAIN_TEXT
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
