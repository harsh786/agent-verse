"""EmailParser — extracts plain text from raw RFC-5322 email messages.

Handles multipart messages and strips HTML to plain text automatically.
"""

from __future__ import annotations

import email as email_lib
import re
from email.message import Message

_HTML_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    text = _HTML_TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


class EmailParser:
    """Parse a raw email string into structured text for ingestion."""

    def parse(self, raw_email: str) -> list[str]:
        """Return a list of text chunks from the email (subject + body parts)."""
        try:
            msg: Message = email_lib.message_from_string(raw_email)
        except Exception:
            return [raw_email] if raw_email.strip() else []

        parts: list[str] = []

        # Subject line
        subject = msg.get("Subject", "").strip()
        if subject:
            parts.append(f"Subject: {subject}")

        # From / To context
        from_hdr = msg.get("From", "").strip()
        to_hdr = msg.get("To", "").strip()
        if from_hdr or to_hdr:
            parts.append(f"From: {from_hdr}  To: {to_hdr}".strip())

        # Body
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        text = payload.decode(part.get_content_charset("utf-8"), errors="replace")
                        if text.strip():
                            parts.append(text.strip())
                elif ct == "text/html":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        html = payload.decode(part.get_content_charset("utf-8"), errors="replace")
                        text = _html_to_text(html)
                        if text:
                            parts.append(text)
        else:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                text = payload.decode(msg.get_content_charset("utf-8"), errors="replace")
                ct = msg.get_content_type()
                if ct == "text/html":
                    text = _html_to_text(text)
                if text.strip():
                    parts.append(text.strip())

        return parts if parts else [raw_email.strip()]

    def parse_metadata(self, raw_email: str) -> dict[str, str]:
        """Extract key metadata fields from a raw email."""
        try:
            msg = email_lib.message_from_string(raw_email)
        except Exception:
            return {}
        return {
            "subject": msg.get("Subject", "").strip(),
            "from": msg.get("From", "").strip(),
            "to": msg.get("To", "").strip(),
            "date": msg.get("Date", "").strip(),
            "message_id": msg.get("Message-ID", "").strip(),
        }
