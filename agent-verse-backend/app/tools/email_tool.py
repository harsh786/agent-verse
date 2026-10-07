"""Email sending and reading tool.

Sending: aiosmtplib (async SMTP)
Reading: aioimaplib (async IMAP)
Credentials: per-tenant configuration (SMTP host/port/user/pass)
"""

from __future__ import annotations

import contextlib
import os
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

        import uuid
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        import aiosmtplib

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
                status, msg_data = await imap.fetch(msg_id.decode(), "(RFC822)")
                if status != "OK" or not msg_data:
                    continue

                import email as _email_lib

                msg = _email_lib.message_from_bytes(msg_data[1])
                body_preview = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body_preview = part.get_payload(decode=True).decode(
                                "utf-8", errors="replace"
                            )[:500]
                            break
                else:
                    body_preview = msg.get_payload(decode=True).decode("utf-8", errors="replace")[
                        :500
                    ]

                messages.append(
                    {
                        "message_id": msg.get("Message-ID", ""),
                        "from": msg.get("From", ""),
                        "subject": msg.get("Subject", ""),
                        "date": msg.get("Date", ""),
                        "body_preview": body_preview,
                    }
                )
        finally:
            with contextlib.suppress(Exception):
                await imap.logout()

        return messages

    @classmethod
    def from_vault_config(cls, vault_config: dict[str, Any]) -> EmailTool:
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
                from_address=vault_config.get("smtp_from", vault_config.get("smtp_username", "")),
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


def platform_sender() -> str:
    """The only ``From`` address the platform SMTP relay may send as.

    ``SMTP_FROM`` (the operator-verified sender) → ``SMTP_USER`` → a local
    placeholder. Tenant input never reaches the ``From`` header: the platform
    relay is authenticated with *platform* credentials, so honouring a
    caller-supplied From let any tenant send mail that passes SPF/DKIM as e.g.
    ``ceo@bank.com`` or another tenant's domain.
    """
    return (
        os.getenv("SMTP_FROM", "").strip()
        or os.getenv("SMTP_USER", "").strip()
        or "noreply@agentverse.local"
    )


def _check_header_safe(value: str, what: str) -> None:
    if "\r" in value or "\n" in value:
        raise ValueError(f"{what} must not contain line breaks")


def _validate_outgoing(
    sender: str,
    recipients: list[str],
    subject: str,
    *,
    from_addr: str | None,
    reply_to: str | None,
    sender_label: str,
) -> None:
    """Shared checks of both relays. Raises ValueError (the caller answers 400)."""
    if from_addr and from_addr.strip().lower() != sender.lower():
        raise ValueError(
            f"from_addr {from_addr!r} is not the {sender_label}; use reply_to to direct replies"
        )
    if not recipients:
        raise ValueError("at least one recipient is required")
    for addr in recipients:
        _check_header_safe(addr, "recipient")
        _validate_email(addr)
    if reply_to:
        _check_header_safe(reply_to, "reply_to")
        _validate_email(reply_to)
    _check_header_safe(subject, "subject")


def _build_message(
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
    *,
    reply_to: str | None,
    tenant_id: str,
) -> Any:
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    if reply_to:
        msg["Reply-To"] = reply_to
    if tenant_id:
        msg["X-AgentVerse-Tenant"] = tenant_id
    msg.attach(MIMEText(body, "plain"))
    return msg


async def email_send(
    to: str | list[str],
    subject: str,
    body: str,
    *,
    from_addr: str | None = None,
    reply_to: str | None = None,
    tenant_id: str = "",
) -> dict[str, Any]:
    """Send an email via aiosmtplib using environment-variable SMTP config.

    For local dev, point SMTP_HOST=localhost SMTP_PORT=1025 (MailHog).
    Returns ``{"success": True, ...}`` or ``{"success": False, "error": ...}``.

    ``From`` is always :func:`platform_sender`. A ``from_addr`` other than that
    address is refused (fail closed) rather than silently rewritten; a caller
    that wants replies elsewhere passes ``reply_to``.

    This is the PLATFORM relay: it never reads a tenant's email settings (its
    SMTP sender or allowlist). The agent email tool (``POST /tools/email/send``)
    applies those before choosing between it and :func:`email_send_tenant_smtp`.
    """
    sender = platform_sender()
    recipients = [to] if isinstance(to, str) else list(to)
    try:
        _validate_outgoing(
            sender,
            recipients,
            subject,
            from_addr=from_addr,
            reply_to=reply_to,
            sender_label="platform-verified sender",
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc), "rejected": True}

    try:
        import aiosmtplib
    except ImportError:
        return {
            "success": False,
            "error": "aiosmtplib not installed. Run: pip install aiosmtplib",
        }

    host = os.getenv("SMTP_HOST", "localhost")
    port = int(os.getenv("SMTP_PORT", "1025"))
    username = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    use_tls = os.getenv("SMTP_TLS", "false").lower() in {"true", "1"}

    msg = _build_message(sender, recipients, subject, body, reply_to=reply_to, tenant_id=tenant_id)

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
        # The relay is the PLATFORM's: its host, port and auth failure text are
        # logged here with a correlation id and never returned to the tenant.
        import logging
        import uuid

        error_id = uuid.uuid4().hex[:12]
        logging.getLogger(__name__).error(
            "email_relay_send_failed error_id=%s tenant_id=%s error=%s",
            error_id,
            tenant_id,
            exc,
        )
        return {
            "success": False,
            "error": f"email delivery failed (error id {error_id})",
            "error_id": error_id,
        }


async def email_send_tenant_smtp(
    target: Any,
    secret: str | None,
    to: str | list[str],
    subject: str,
    body: str,
    *,
    from_addr: str | None = None,
    reply_to: str | None = None,
    tenant_id: str = "",
) -> dict[str, Any]:
    """Send through the tenant's OWN SMTP server (``target``: a
    :class:`app.tools.tenant_smtp.SMTPTarget`), as its configured From address.

    Same contract as :func:`email_send`: ``{"success": True, ...}``, or
    ``{"success": False, "error": ..., "rejected": True}`` for a bad request,
    or ``{"success": False, "error": ...}`` for a delivery failure. The server
    is the tenant's, so the failing stage is reported (never the credentials).
    System mail never comes here: it always uses the platform relay.
    """
    from app.tools import tenant_smtp

    sender = target.from_address
    recipients = [to] if isinstance(to, str) else list(to)
    try:
        _validate_outgoing(
            sender,
            recipients,
            subject,
            from_addr=from_addr,
            reply_to=reply_to,
            sender_label="tenant SMTP sender",
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc), "rejected": True}

    msg = _build_message(sender, recipients, subject, body, reply_to=reply_to, tenant_id=tenant_id)
    outcome = await tenant_smtp.deliver(target, secret, msg, recipients)
    if outcome.ok:
        return {"success": True, "to": recipients, "subject": subject, "relay": "tenant"}
    import logging
    import uuid

    error_id = uuid.uuid4().hex[:12]
    logging.getLogger(__name__).warning(
        "tenant_smtp_send_failed error_id=%s tenant_id=%s stage=%s code=%s",
        error_id,
        tenant_id,
        outcome.stage,
        outcome.code,
    )
    detail = f"tenant SMTP delivery failed at {outcome.stage}: {outcome.message}"
    if outcome.code is not None:
        detail += f" (SMTP {outcome.code})"
    return {
        "success": False,
        "error": f"{detail} (error id {error_id})",
        "error_id": error_id,
        "stage": outcome.stage,
    }
