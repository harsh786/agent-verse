"""Email-to-goal: monitor an IMAP mailbox and convert emails to AgentVerse goals.

Configuration via environment variables:
  IMAP_HOST     — IMAP server hostname
  IMAP_PORT     — IMAP port (default 993 for SSL, 143 for STARTTLS)
  IMAP_USER     — email address to monitor
  IMAP_PASSWORD — email password
  IMAP_SSL      — "true" for SSL (default), "false" for STARTTLS
  IMAP_MAILBOX  — mailbox to monitor (default "INBOX")
  IMAP_ENABLED  — "true" to enable (default "false")

Emails are processed as follows:
  Subject → goal text
  From    → used as context (tenant lookup by email)
  Body    → additional context appended to goal
"""
from __future__ import annotations

import email
import os
from email.header import decode_header
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _is_enabled() -> bool:
    return os.getenv("IMAP_ENABLED", "false").lower() in {"true", "1", "yes"}


def _get_config() -> dict[str, Any]:
    return {
        "host": os.getenv("IMAP_HOST", ""),
        "port": int(os.getenv("IMAP_PORT", "993")),
        "user": os.getenv("IMAP_USER", ""),
        "password": os.getenv("IMAP_PASSWORD", ""),
        "ssl": os.getenv("IMAP_SSL", "true").lower() not in {"false", "0"},
        "mailbox": os.getenv("IMAP_MAILBOX", "INBOX"),
    }


def _decode_header_value(value: str) -> str:
    """Decode email header (handles encoded headers like =?UTF-8?...)."""
    decoded_parts = decode_header(value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result)


async def check_and_process_emails(goal_service: Any, tenant_ctx: Any) -> int:
    """Check IMAP mailbox and submit new emails as goals.

    Returns number of emails processed.
    """
    if not _is_enabled():
        return 0

    config = _get_config()
    if not config["host"] or not config["user"]:
        return 0

    try:
        import aioimaplib  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("aioimaplib not installed — email-to-goal disabled")
        return 0

    processed = 0
    try:
        if config["ssl"]:
            imap = aioimaplib.IMAP4_SSL(
                host=config["host"], port=config["port"]
            )
        else:
            imap = aioimaplib.IMAP4(
                host=config["host"], port=config["port"]
            )

        await imap.wait_hello_from_server()
        await imap.login(config["user"], config["password"])
        await imap.select(config["mailbox"])

        # Search for unread emails
        status, messages = await imap.search("UNSEEN")
        if status != "OK" or not messages[0]:
            await imap.logout()
            return 0

        email_ids = messages[0].split()
        for email_id in email_ids[:10]:  # Process max 10 at a time
            status, msg_data = await imap.fetch(email_id, "(RFC822)")
            if status != "OK":
                continue

            raw_email = msg_data[1]
            msg = email.message_from_bytes(raw_email)

            subject = _decode_header_value(msg.get("Subject", "No subject"))
            from_addr = msg.get("From", "")

            # Extract body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(
                            "utf-8", errors="replace"
                        )[:500]
                        break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")[:500]

            # Build goal from email
            goal_text = subject
            if body.strip():
                goal_text += f"\n\nAdditional context from email:\n{body.strip()}"

            try:
                result = await goal_service.submit_goal(
                    goal=goal_text,
                    priority="normal",
                    dry_run=False,
                    tenant_ctx=tenant_ctx,
                )
                logger.info(
                    "email_converted_to_goal",
                    from_addr=from_addr,
                    goal_id=result["goal_id"],
                    subject=subject[:100],
                )
                processed += 1

                # Mark as read
                await imap.store(email_id, "+FLAGS", r"(\Seen)")
            except Exception as exc:
                logger.warning("email_goal_submission_failed", error=str(exc))

        await imap.logout()
    except Exception as exc:
        logger.warning("imap_check_failed", error=str(exc))

    return processed
