"""Email sending and reading tool.

Sending: aiosmtplib (async SMTP)
Reading: aioimaplib (async IMAP)
Credentials: per-tenant configuration (SMTP host/port/user/pass)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _validate_email(address: str) -> None:
    if not _EMAIL_RE.match(address):
        raise ValueError(f"Invalid email address: {address!r}")


@dataclass
class SMTPConfig:
    """SMTP connection configuration."""

    host: str
    username: str
    password: str
    from_address: str
    port: int = 587
    use_tls: bool = True


@dataclass
class IMAPConfig:
    """IMAP connection configuration."""

    host: str
    username: str
    password: str
    port: int = 993
    use_ssl: bool = True


class EmailTool:
    """Async email tool for sending and reading emails."""

    def __init__(
        self,
        smtp_config: SMTPConfig | None = None,
        imap_config: IMAPConfig | None = None,
    ) -> None:
        self._smtp = smtp_config
        self._imap = imap_config

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        html_body: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send an email via SMTP.

        Returns {"status": "sent", "message_id": ...} on success.
        Raises ValueError for invalid addresses or missing SMTP config.
        """
        if self._smtp is None:
            raise ValueError("SMTP not configured for this agent. Set smtp_config.")

        _validate_email(to)
        for addr in (cc or []) + (bcc or []):
            _validate_email(addr)

        import aiosmtplib
        import uuid
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["From"] = self._smtp.from_address
        msg["To"] = to
        msg["Subject"] = subject
        message_id = f"<{uuid.uuid4().hex}@agentverse>"
        msg["Message-ID"] = message_id

        if cc:
            msg["Cc"] = ", ".join(cc)

        msg.attach(MIMEText(body, "plain"))
        if html_body:
            msg.attach(MIMEText(html_body, "html"))

        recipients = [to] + (cc or []) + (bcc or [])

        await aiosmtplib.send(
            msg,
            hostname=self._smtp.host,
            port=self._smtp.port,
            username=self._smtp.username,
            password=self._smtp.password,
            use_tls=self._smtp.use_tls,
        )

        return {
            "status": "sent",
            "to": to,
            "subject": subject,
            "message_id": message_id,
            "recipients": recipients,
        }

    async def read_inbox(
        self, limit: int = 10, folder: str = "INBOX", unread_only: bool = False
    ) -> list[dict[str, Any]]:
        """Read emails from IMAP inbox.

        Returns list of message dicts with from, subject, date, body preview.
        """
        if self._imap is None:
            raise ValueError("IMAP not configured for this agent. Set imap_config.")

        import aioimaplib

        messages: list[dict[str, Any]] = []
        imap = aioimaplib.IMAP4_SSL(
            host=self._imap.host,
            port=self._imap.port,
            timeout=30,
        )
        try:
            await imap.wait_hello_from_server()
            await imap.login(self._imap.username, self._imap.password)
            await imap.select(folder)

            search_criteria = "UNSEEN" if unread_only else "ALL"
            status, data = await imap.search(search_criteria)
            if status != "OK":
                return []

            message_ids = data[0].split()
            # Fetch most recent `limit` messages
            fetch_ids = message_ids[-limit:]

            for msg_id in reversed(fetch_ids):
                status, msg_data = await imap.fetch(
                    msg_id.decode(), "(RFC822)"
                )
                if status != "OK" or not msg_data:
                    continue

                import email as _email_lib

                msg = _email_lib.message_from_bytes(msg_data[1])
                body_preview = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body_preview = (
                                part.get_payload(decode=True)
                                .decode("utf-8", errors="replace")[:500]
                            )
                            break
                else:
                    body_preview = (
                        msg.get_payload(decode=True)
                        .decode("utf-8", errors="replace")[:500]
                    )

                messages.append({
                    "message_id": msg.get("Message-ID", ""),
                    "from": msg.get("From", ""),
                    "subject": msg.get("Subject", ""),
                    "date": msg.get("Date", ""),
                    "body_preview": body_preview,
                })
        finally:
            try:
                await imap.logout()
            except Exception:
                pass

        return messages

    @classmethod
    def from_vault_config(
        cls, vault_config: dict[str, Any]
    ) -> "EmailTool":
        """Create EmailTool from a vault/secrets config dict.

        Expected keys: smtp_host, smtp_port, smtp_username, smtp_password,
                       smtp_from, imap_host, imap_port, imap_username, imap_password
        """
        smtp = None
        if vault_config.get("smtp_host"):
            smtp = SMTPConfig(
                host=vault_config["smtp_host"],
                port=int(vault_config.get("smtp_port", 587)),
                username=vault_config.get("smtp_username", ""),
                password=vault_config.get("smtp_password", ""),
                from_address=vault_config.get(
                    "smtp_from", vault_config.get("smtp_username", "")
                ),
                use_tls=bool(vault_config.get("smtp_use_tls", True)),
            )
        imap = None
        if vault_config.get("imap_host"):
            imap = IMAPConfig(
                host=vault_config["imap_host"],
                port=int(vault_config.get("imap_port", 993)),
                username=vault_config.get("imap_username", ""),
                password=vault_config.get("imap_password", ""),
            )
        return cls(smtp_config=smtp, imap_config=imap)


# ── Module-level convenience wrapper for simple SMTP sends ────────────────────

import os as _os


async def email_send(
    to: str | list[str],
    subject: str,
    body: str,
    *,
    from_addr: str | None = None,
) -> dict[str, Any]:
    """Send an email via aiosmtplib using environment-variable SMTP config.

    For local dev, point SMTP_HOST=localhost SMTP_PORT=1025 (MailHog).
    Returns ``{"success": True, ...}`` or ``{"success": False, "error": ...}``.
    """
    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
    except ImportError:
        return {
            "success": False,
            "error": "aiosmtplib not installed. Run: pip install aiosmtplib",
        }

    host = _os.getenv("SMTP_HOST", "localhost")
    port = int(_os.getenv("SMTP_PORT", "1025"))
    username = _os.getenv("SMTP_USER", "")
    password = _os.getenv("SMTP_PASSWORD", "")
    use_tls = _os.getenv("SMTP_TLS", "false").lower() in {"true", "1"}

    recipients = [to] if isinstance(to, str) else to
    sender = from_addr or username or "noreply@agentverse.local"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body, "plain"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=host,
            port=port,
            username=username or None,
            password=password or None,
            use_tls=use_tls,
        )
        return {"success": True, "to": recipients, "subject": subject}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
