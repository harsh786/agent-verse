"""ContentClassifier — detects content type from text or filename."""

from __future__ import annotations

import enum
import re


class ContentType(enum.StrEnum):
    TEXT = "text"
    PDF = "pdf"
    DOCX = "docx"
    HTML = "html"
    MARKDOWN = "markdown"
    CODE = "code"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    CSV = "csv"
    JSON = "json"
    WEB_PAGE = "web_page"
    MIXED = "mixed"
    # Extended types for new parsers
    EXCEL = "excel"  # .xlsx / .xls / .ods
    YAML = "yaml"  # .yaml / .yml / .toml / .hcl
    PARQUET = "parquet"  # Apache Parquet
    AVRO = "avro"  # Apache Avro
    LATEX = "latex"  # LaTeX source
    NOTEBOOK = "notebook"  # Jupyter .ipynb


_EXT_MAP: dict[str, ContentType] = {
    ".pdf": ContentType.PDF,
    ".docx": ContentType.DOCX,
    ".doc": ContentType.DOCX,
    ".html": ContentType.HTML,
    ".htm": ContentType.HTML,
    ".md": ContentType.MARKDOWN,
    ".markdown": ContentType.MARKDOWN,
    ".py": ContentType.CODE,
    ".js": ContentType.CODE,
    ".ts": ContentType.CODE,
    ".java": ContentType.CODE,
    ".go": ContentType.CODE,
    ".rs": ContentType.CODE,
    ".cpp": ContentType.CODE,
    ".c": ContentType.CODE,
    ".rb": ContentType.CODE,
    ".sh": ContentType.CODE,
    ".sql": ContentType.CODE,
    ".png": ContentType.IMAGE,
    ".jpg": ContentType.IMAGE,
    ".jpeg": ContentType.IMAGE,
    ".gif": ContentType.IMAGE,
    ".webp": ContentType.IMAGE,
    ".svg": ContentType.IMAGE,
    ".mp3": ContentType.AUDIO,
    ".wav": ContentType.AUDIO,
    ".ogg": ContentType.AUDIO,
    ".mp4": ContentType.VIDEO,
    ".mov": ContentType.VIDEO,
    ".avi": ContentType.VIDEO,
    ".csv": ContentType.CSV,
    ".tsv": ContentType.CSV,
    ".json": ContentType.JSON,
    ".jsonl": ContentType.JSON,
    # Extended
    ".xlsx": ContentType.EXCEL,
    ".xls": ContentType.EXCEL,
    ".ods": ContentType.EXCEL,
    ".yaml": ContentType.YAML,
    ".yml": ContentType.YAML,
    ".toml": ContentType.YAML,
    ".hcl": ContentType.YAML,
    ".parquet": ContentType.PARQUET,
    ".avro": ContentType.AVRO,
    ".tex": ContentType.LATEX,
    ".latex": ContentType.LATEX,
    ".ipynb": ContentType.NOTEBOOK,
}

# MIME content-type → ContentType for core binary/structured formats. Prefixes
# (image/, audio/, video/) are handled separately in classify_mime().
_MIME_MAP: dict[str, ContentType] = {
    "application/pdf": ContentType.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ContentType.DOCX,
    "application/msword": ContentType.DOCX,
    "text/csv": ContentType.CSV,
    "text/tab-separated-values": ContentType.CSV,
}

_CODE_PATTERNS = re.compile(
    r"(?m)^(?:def |class |import |from .+ import |function |const |let |var |public class )"
)
_HTML_PATTERN = re.compile(r"<(?:html|body|div|span|p|h[1-6]|script|style)", re.I)
_JSON_PATTERN = re.compile(r"^\s*[\[\{]")
_MARKDOWN_PATTERN = re.compile(r"(?m)^#{1,6}\s|^\*\*|^-\s|^\d+\.\s")


class ContentClassifier:
    def classify(self, content: str) -> ContentType:
        if _HTML_PATTERN.search(content[:500]):
            return ContentType.HTML
        if _JSON_PATTERN.match(content[:20]):
            return ContentType.JSON
        if _CODE_PATTERNS.search(content[:1000]):
            return ContentType.CODE
        if _MARKDOWN_PATTERN.search(content[:500]):
            return ContentType.MARKDOWN
        return ContentType.TEXT

    def classify_by_filename(self, filename: str) -> ContentType:
        import os

        _, ext = os.path.splitext(filename.lower())
        return _EXT_MAP.get(ext, ContentType.TEXT)

    def classify_mime(self, mime_type: str) -> ContentType | None:
        """Map a MIME content-type to a ContentType, or None if unrecognised.

        Preferred over content sniffing when a connector supplies a trustworthy
        MIME type (e.g. ``application/pdf``). Returns None for generic/unknown
        types (like ``text/plain``) so the caller can fall back to sniffing.
        """
        if not mime_type:
            return None
        mt = mime_type.split(";", 1)[0].strip().lower()
        if mt in _MIME_MAP:
            return _MIME_MAP[mt]
        if mt.startswith("image/"):
            return ContentType.IMAGE
        if mt.startswith("audio/"):
            return ContentType.AUDIO
        if mt.startswith("video/"):
            return ContentType.VIDEO
        return None
