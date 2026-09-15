"""Phase 4 — chat attachment parsing + context formatting."""

from __future__ import annotations

import json

from app.chat.attachments import format_attachment_context, parse_attachment


async def test_parse_text_attachment() -> None:
    parsed = await parse_attachment(b"hello from a file", "notes.txt")
    assert "hello from a file" in parsed["content"]
    assert "error" not in parsed


async def test_parse_json_attachment() -> None:
    parsed = await parse_attachment(json.dumps({"k": "v"}).encode(), "data.json")
    assert "k" in parsed["content"]


def test_format_context_block() -> None:
    ctx = format_attachment_context({"filename": "a.txt", "format": "text", "content": "body"})
    assert ctx.startswith("[Attached file: a.txt (text)]")
    assert "body" in ctx


def test_format_truncates_long_content() -> None:
    ctx = format_attachment_context(
        {"filename": "big.txt", "format": "text", "content": "x" * 20000}, max_chars=100
    )
    assert "(truncated)" in ctx and len(ctx) < 400


def test_format_error() -> None:
    ctx = format_attachment_context({"filename": "bad.pdf", "error": "corrupt"})
    assert "could not be parsed" in ctx and "bad.pdf" in ctx
