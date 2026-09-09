"""Tests for new ingestion connectors and parsers."""
from __future__ import annotations

import pytest

from app.ingestion.parsers.email_parser import EmailParser


class TestEmailParser:
    def setup_method(self) -> None:
        self.parser = EmailParser()

    def test_parse_plain_email(self) -> None:
        raw = (
            "From: alice@example.com\r\n"
            "To: bob@example.com\r\n"
            "Subject: Test Email\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
            "Hello Bob, this is a test email."
        )
        parts = self.parser.parse(raw)
        assert any("Test Email" in p for p in parts)
        assert any("Hello Bob" in p for p in parts)

    def test_parse_metadata(self) -> None:
        raw = (
            "From: alice@example.com\r\n"
            "To: bob@example.com\r\n"
            "Subject: Hello\r\n"
            "Message-ID: <12345@example.com>\r\n"
            "\r\n"
            "Body text."
        )
        meta = self.parser.parse_metadata(raw)
        assert meta["subject"] == "Hello"
        assert meta["from"] == "alice@example.com"
        assert meta["to"] == "bob@example.com"
        assert "12345" in meta["message_id"]

    def test_parse_empty_email_returns_fallback(self) -> None:
        result = self.parser.parse("Not really an email")
        assert len(result) >= 1

    def test_parse_html_email_strips_tags(self) -> None:
        raw = (
            "From: alice@example.com\r\n"
            "Content-Type: text/html\r\n"
            "\r\n"
            "<html><body><p>Hello <b>World</b></p></body></html>"
        )
        parts = self.parser.parse(raw)
        combined = " ".join(parts)
        assert "Hello" in combined
        assert "World" in combined
        assert "<" not in combined  # HTML tags stripped


class TestNotionConnector:
    def test_blocks_to_text_extracts_plain_text(self) -> None:
        from app.ingestion.connectors.notion_connector import NotionConnector
        connector = NotionConnector(api_key="test")
        blocks = [
            {
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"plain_text": "Hello World"},
                        {"plain_text": " More text"},
                    ]
                },
            },
            {
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [{"plain_text": "Section Header"}]
                },
            },
        ]
        result = connector._blocks_to_text(blocks)
        assert "Hello World More text" in result
        assert "Section Header" in result

    def test_blocks_to_text_handles_empty(self) -> None:
        from app.ingestion.connectors.notion_connector import NotionConnector
        connector = NotionConnector(api_key="test")
        result = connector._blocks_to_text([])
        assert result == ""

    def test_blocks_to_text_skips_blank_blocks(self) -> None:
        from app.ingestion.connectors.notion_connector import NotionConnector
        connector = NotionConnector(api_key="test")
        blocks = [
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "   "}]}},
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Real content"}]}},
        ]
        result = connector._blocks_to_text(blocks)
        assert "Real content" in result
        # Blank block should not appear
        assert result.strip() == "Real content"
