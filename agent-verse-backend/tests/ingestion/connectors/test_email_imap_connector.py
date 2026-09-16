"""Tests for EmailIMAPConnector — validate_connection, get_delta, header
decoding, and body extraction helpers. imaplib is mocked throughout (no real
network)."""
from __future__ import annotations

import email as email_mod
from unittest.mock import MagicMock, patch

from app.ingestion.connectors.email_imap_connector import (
    EmailIMAPConnector,
    _decode,
    _extract_text,
)
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-e",
        tenant_id="t1",
        name="Test IMAP",
        family="communication",
        source_type="imap",
        enabled=True,
        connection_config=conn_config or {"host": "imap.example.com", "username": "u", "password": "p"},
    )


def _build_raw_email(subject="Hello", frm="a@b.com", body="Hi there") -> bytes:
    msg = email_mod.message.EmailMessage()
    msg["Subject"] = subject
    msg["From"] = frm
    msg["Date"] = "Mon, 01 Jan 2026 00:00:00 +0000"
    msg.set_content(body)
    return msg.as_bytes()


class TestDecodeHelper:
    def test_none_returns_empty(self):
        assert _decode(None) == ""

    def test_bytes_decoded(self):
        assert _decode(b"hello") == "hello"

    def test_plain_string_passthrough(self):
        assert _decode("plain subject") == "plain subject"

    def test_encoded_header_decoded(self):
        # RFC 2047 encoded-word
        encoded = "=?utf-8?q?Caf=C3=A9?="
        assert "Caf" in _decode(encoded)


class TestExtractText:
    def test_simple_message(self):
        msg = email_mod.message_from_bytes(_build_raw_email(body="plain text body"))
        text = _extract_text(msg)
        assert "plain text body" in text

    def test_multipart_message(self):
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart()
        msg["Subject"] = "Multipart"
        msg.attach(MIMEText("plain part text", "plain"))
        msg.attach(MIMEText("<p>html part</p>", "html"))
        parsed = email_mod.message_from_bytes(msg.as_bytes())

        assert parsed.is_multipart() is True
        text = _extract_text(parsed)
        assert "plain part text" in text
        assert "html part" not in text  # only text/plain parts are extracted


class TestValidateConnection:
    async def test_success_ssl(self):
        mock_conn = MagicMock()
        mock_conn.list.return_value = ("OK", [b"mailbox1", b"mailbox2"])
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn) as mock_ssl:
            connector = EmailIMAPConnector()
            health = await connector.validate_connection(_make_config())
        mock_ssl.assert_called_once()
        mock_conn.login.assert_called_once_with("u", "p")
        assert health.ok is True
        assert health.metadata["mailboxes"] == 2

    async def test_success_non_ssl(self):
        mock_conn = MagicMock()
        mock_conn.list.return_value = ("OK", [b"mailbox1"])
        with patch("imaplib.IMAP4", return_value=mock_conn) as mock_plain:
            connector = EmailIMAPConnector()
            health = await connector.validate_connection(_make_config({"ssl": False, "host": "h", "port": 143}))
        mock_plain.assert_called_once()
        assert health.ok is True

    async def test_login_failure(self):
        mock_conn = MagicMock()
        mock_conn.login.side_effect = Exception("auth failed")
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            connector = EmailIMAPConnector()
            health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "auth failed" in health.error


class TestGetDelta:
    async def test_fetches_and_yields_messages(self):
        mock_conn = MagicMock()
        mock_conn.uid.side_effect = _make_uid_side_effect(
            search_uids=[b"1", b"2"],
            messages={"1": _build_raw_email(subject="First", body="Body one"),
                      "2": _build_raw_email(subject="Second", body="Body two")},
        )
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            connector = EmailIMAPConnector()
            config = _make_config({"host": "h", "mailbox": "INBOX", "batch_size": 10})
            results = [d async for d in connector.get_delta(config, None)]

        assert len(results) == 2
        doc0, cursor0 = results[0]
        assert "First" in doc0.content.decode()
        assert doc0.metadata["subject"] == "First"
        doc1, cursor1 = results[1]
        assert cursor1 == "2"

    async def test_cursor_advances_numerically_not_lexicographically(self):
        """Regression: UID cursor must compare numerically (e.g. '9' < '10'),
        not as strings, or the sync cursor can regress and cause re-fetching."""
        mock_conn = MagicMock()
        mock_conn.uid.side_effect = _make_uid_side_effect(
            search_uids=[b"9", b"10"],
            messages={"9": _build_raw_email(subject="Nine"), "10": _build_raw_email(subject="Ten")},
        )
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            connector = EmailIMAPConnector()
            config = _make_config({"host": "h"})
            results = [d async for d in connector.get_delta(config, None)]

        final_cursor = results[-1][1]
        assert final_cursor == "10"

    async def test_no_new_messages_yields_nothing(self):
        mock_conn = MagicMock()
        mock_conn.uid.side_effect = _make_uid_side_effect(search_uids=[], messages={})
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            connector = EmailIMAPConnector()
            results = [d async for d in connector.get_delta(_make_config(), "5")]
        assert results == []

    async def test_missing_fetch_data_skipped(self):
        mock_conn = MagicMock()

        def uid(cmd, *args):
            if cmd == "SEARCH":
                return "OK", [b"1"]
            if cmd == "FETCH":
                return "OK", [None]  # simulate missing data
            return "OK", [None]

        mock_conn.uid.side_effect = uid
        with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
            connector = EmailIMAPConnector()
            results = [d async for d in connector.get_delta(_make_config(), None)]
        assert results == []


def _make_uid_side_effect(search_uids: list[bytes], messages: dict[str, bytes]):
    def uid(cmd, *args):
        if cmd == "SEARCH":
            return "OK", [b" ".join(search_uids)] if search_uids else [b""]
        if cmd == "FETCH":
            uid_str = args[0]
            raw = messages.get(uid_str)
            if raw is None:
                return "OK", [None]
            return "OK", [(b"1 (RFC822 {%d}" % len(raw), raw)]
        return "OK", [None]

    return uid


def test_source_type_and_registration():
    from app.ingestion.connector_registry import get_connector

    assert EmailIMAPConnector().source_type == "imap"
    assert get_connector("imap") is EmailIMAPConnector
    assert get_connector("gmail") is EmailIMAPConnector
