"""EmailIMAPConnector — Gmail and IMAP email ingestion.

Uses imaplib for generic IMAP access.
Cursor: UID of the last fetched message.
Supports: Gmail (IMAP enabled), Outlook, Exchange (IMAP), and any RFC 3501 server.
"""

from __future__ import annotations

import email
import imaplib
import logging
import uuid
from collections.abc import AsyncIterator
from email.header import decode_header as _decode_header
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    parts = _decode_header(value)
    result = []
    for b, charset in parts:
        if isinstance(b, bytes):
            result.append(b.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(b)
    return " ".join(result)


def _extract_text(msg: email.message.Message) -> str:
    """Extract plain-text body from a multipart email."""
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode("utf-8", errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            parts.append(payload.decode("utf-8", errors="replace"))
    return "\n".join(parts)


@register("imap", feature_flag="ingestion_connector_imap_enabled")
@register("gmail")
class EmailIMAPConnector(BaseConnector):
    """IMAP email connector — supports Gmail, Outlook, and any IMAP server."""

    source_type = "imap"
    supports_deletion_tracking = False

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            cc = config.connection_config
            host = cc.get("host", "imap.gmail.com")
            port = int(cc.get("port", 993))
            ssl = cc.get("ssl", True)
            user = cc.get("username", "")
            password = cc.get("password", "")

            conn = imaplib.IMAP4_SSL(host, port) if ssl else imaplib.IMAP4(host, port)

            conn.login(user, password)
            _typ, mboxes = conn.list()
            conn.logout()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"mailboxes": len(mboxes or []), "user": user},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        import asyncio

        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        host = cc.get("host", "imap.gmail.com")
        port = int(cc.get("port", 993))
        ssl = cc.get("ssl", True)
        user = cc.get("username", "")
        password = cc.get("password", "")
        mailbox = cc.get("mailbox", "INBOX")
        batch_size = int(cc.get("batch_size", 100))

        # IMAP is sync — run in executor
        def _fetch_emails() -> list[tuple[str, str, dict]]:
            conn = imaplib.IMAP4_SSL(host, port) if ssl else imaplib.IMAP4(host, port)
            conn.login(user, password)
            conn.select(mailbox)

            last_uid = cursor or "0"
            # NOTE: unlike search(), uid() has no charset special-casing for
            # SEARCH — passing None here would be stringified into the raw
            # IMAP command ("SEARCH None UID ...") and silently match nothing.
            _typ, data = conn.uid("SEARCH", f"UID {int(last_uid) + 1}:*")
            uids = data[0].split() if data and data[0] else []
            uids = uids[:batch_size]

            results = []
            for uid_bytes in uids:
                uid = uid_bytes.decode()
                _typ, msg_data = conn.uid("FETCH", uid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                subject = _decode(msg.get("Subject"))
                from_addr = _decode(msg.get("From"))
                date = msg.get("Date", "")
                body = _extract_text(msg)
                text = f"Subject: {subject}\nFrom: {from_addr}\nDate: {date}\n\n{body}"
                results.append((uid, text, {"subject": subject, "from": from_addr, "date": date}))

            conn.logout()
            return results

        loop = asyncio.get_event_loop()
        messages = await loop.run_in_executor(None, _fetch_emails)

        new_cursor = cursor or "0"
        for uid, text, meta in messages:
            # IMAP UIDs are numeric strings — compare as ints, not lexicographically,
            # or the cursor can regress (e.g. "9" > "10" as strings) and cause
            # already-fetched messages to be re-searched on the next sync.
            new_cursor = str(max(int(new_cursor), int(uid)))
            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"imap://{host}/{mailbox}/uid/{uid}",
                content=text.encode(),
                content_type="text/plain",
                metadata=meta,
            )
            yield doc, new_cursor
