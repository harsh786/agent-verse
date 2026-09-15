"""Chat attachment ingestion (Phase 4, input side).

Parses an uploaded file into text via the existing DocumentParserTool and formats
it as context for a chat turn, so "here's a file, summarize it" works. Local
parsing only (PDF/CSV/DOCX/TXT/MD/JSON/YAML).
"""

from __future__ import annotations

from typing import Any

_MAX_ATTACHMENT_CHARS = 8000


async def parse_attachment(content_bytes: bytes, filename: str = "document") -> dict[str, Any]:
    """Parse an attachment's bytes into a dict with 'content' (or 'error')."""
    from app.tools.document_parser import DocumentParserTool

    return await DocumentParserTool().execute(content_bytes=content_bytes, filename=filename)


def format_attachment_context(
    parsed: dict[str, Any], *, max_chars: int = _MAX_ATTACHMENT_CHARS
) -> str:
    """Render a parsed attachment as a labeled context block for the LLM."""
    name = parsed.get("filename", "file")
    if parsed.get("error"):
        return f"[Attached file {name} could not be parsed: {parsed['error']}]"
    content = (parsed.get("content") or "")[:max_chars]
    truncated = " (truncated)" if len(parsed.get("content") or "") > max_chars else ""
    fmt = parsed.get("format", "text")
    return f"[Attached file: {name} ({fmt}){truncated}]\n{content}"
