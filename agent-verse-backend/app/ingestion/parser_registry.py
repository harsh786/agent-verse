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


class ParserRegistry:
    def __init__(self) -> None:
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
        }

    def get_parser(self, content_type: ContentType) -> TextParser:
        return self._parsers.get(content_type, TextParser())  # type: ignore[return-value]
