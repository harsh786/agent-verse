"""Email-to-goal: monitor an IMAP mailbox and convert emails to AgentVerse goals.

Configuration via environment variables:
  IMAP_HOST     — IMAP server hostname
  IMAP_PORT     — IMAP port (default 993 for SSL, 143 for STARTTLS)
  IMAP_USER     — email address to monitor
  IMAP_PASSWORD — email password
  IMAP_SSL      — "true" for SSL (default), "false" for STARTTLS
  IMAP_MAILBOX  — mailbox to monitor (default "INBOX")
  IMAP_ENABLED  — "true" to enable (default "false")
  IMAP_SENDER_ALLOWLIST — comma-separated addresses / @domains allowed to create
                  goals (REQUIRED: empty = every email is dropped)
  IMAP_AUTHSERV_ID — optional: the receiving MTA's authserv-id; when set, only
                  an Authentication-Results header written by it is trusted

Emails are processed as follows:
  Subject → goal text
  From    → used as context (tenant lookup by email)
  Body    → additional context appended to goal
"""

from __future__ import annotations

import contextlib
import email
import hashlib
import os
import re
from email.header import decode_header
from email.utils import parseaddr
from typing import Any

from app.observability.logging import get_logger
from app.services.dedup import GoalDeduplicator

logger = get_logger(__name__)

# Long-lived, message-id-keyed dedup — independent of (and a safety net for) the
# IMAP \Seen flag. The 60s default TTL on the shared goal-submission dedup is far
# shorter than a typical mailbox poll interval, so it can't protect against the
# same email being resubmitted across polls; this instance is dedicated to email
# and kept for a full day.
_EMAIL_DEDUP_TTL_SECONDS = 60 * 60 * 24
_email_dedup = GoalDeduplicator(ttl=_EMAIL_DEDUP_TTL_SECONDS)


def _wire_email_dedup_redis(goal_service: Any) -> None:
    """Best-effort: share the goal service's Redis client so the email dedup
    survives across replicas/restarts, like the goal-submission dedup does."""
    redis = getattr(goal_service, "_redis", None)
    if redis is not None and getattr(_email_dedup, "_redis", None) is None:
        _email_dedup._redis = redis


def _email_identity_key(msg: Any, raw_email: bytes) -> str:
    """Stable per-email identity, independent of the IMAP \\Seen flag.

    Prefers the ``Message-ID`` header (RFC 5322 — globally unique per message).
    Falls back to a hash of the raw bytes for the rare malformed message that
    lacks one, so every email still gets a stable key.
    """
    message_id = (msg.get("Message-ID") or "").strip()
    if message_id:
        return message_id
    return "sha256:" + hashlib.sha256(raw_email).hexdigest()


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


# ── Sender trust (TRG-37) ────────────────────────────────────────────────────
# Every unseen email used to become an autonomous goal, from ANY sender. A
# message must now (1) come from an address or @domain on IMAP_SENDER_ALLOWLIST
# and (2) carry the receiving MTA's Authentication-Results verdict with a
# dkim/spf/dmarc pass aligned to the From domain. Only the TOPMOST
# Authentication-Results header counts (the MTA prepends its own; lower ones can
# be written by the sender); IMAP_AUTHSERV_ID pins which host wrote it.
_AR_COMMENT = re.compile(r"\([^)]*\)")
_AR_PAIR = re.compile(r"([A-Za-z0-9_.-]+)\s*=\s*([^\s;]+)")


def _sender_allowlist() -> tuple[set[str], set[str]]:
    """(allowed addresses, allowed domains) from IMAP_SENDER_ALLOWLIST."""
    addresses: set[str] = set()
    domains: set[str] = set()
    for item in os.getenv("IMAP_SENDER_ALLOWLIST", "").split(","):
        entry = item.strip().lower()
        if entry.startswith("@") and len(entry) > 1:
            domains.add(entry[1:])
        elif "@" in entry:
            addresses.add(entry)
    return addresses, domains


def _aligned(from_domain: str, auth_domain: str) -> bool:
    auth_domain = auth_domain.strip().lower().rstrip(".")
    if "@" in auth_domain:
        auth_domain = auth_domain.rsplit("@", 1)[1]
    return bool(auth_domain) and (
        from_domain == auth_domain or from_domain.endswith("." + auth_domain)
    )


def _auth_results_pass(header: str, from_domain: str) -> tuple[str, bool]:
    """(authserv-id, whether an aligned dkim/spf/dmarc pass is present)."""
    parts = [p.strip() for p in _AR_COMMENT.sub("", header).split(";")]
    authserv_id = parts[0].split()[0].lower() if parts and parts[0] else ""
    for resinfo in parts[1:]:
        pairs = _AR_PAIR.findall(resinfo)
        if not pairs:
            continue
        method, result = pairs[0][0].lower(), pairs[0][1].lower()
        if result != "pass":
            continue
        props = {k.lower(): v for k, v in pairs[1:]}
        if method == "dkim" and _aligned(from_domain, props.get("header.d", "")):
            return authserv_id, True
        if method == "spf" and _aligned(
            from_domain, props.get("smtp.mailfrom", "") or props.get("smtp.helo", "")
        ):
            return authserv_id, True
        if method == "dmarc" and _aligned(from_domain, props.get("header.from", from_domain)):
            return authserv_id, True
    return authserv_id, False


def sender_trust_problem(msg: Any) -> str | None:
    """Why *msg* must not become a goal, or ``None`` when its sender is trusted."""
    addresses, domains = _sender_allowlist()
    if not addresses and not domains:
        return "IMAP_SENDER_ALLOWLIST is empty — no sender is trusted"
    sender = parseaddr(str(msg.get("From", "") or ""))[1].strip().lower()
    from_domain = sender.rsplit("@", 1)[1] if "@" in sender else ""
    if not from_domain or (sender not in addresses and from_domain not in domains):
        return f"sender {sender or '<none>'} is not on IMAP_SENDER_ALLOWLIST"
    results = msg.get_all("Authentication-Results") or []
    if not results:
        return "no Authentication-Results header (sender not verified)"
    authserv_id, passed = _auth_results_pass(str(results[0]), from_domain)
    pinned = os.getenv("IMAP_AUTHSERV_ID", "").strip().lower()
    if pinned and authserv_id != pinned:
        return f"Authentication-Results authserv-id {authserv_id!r} is not {pinned!r}"
    if not passed:
        return "sender not authenticated (no dkim/spf/dmarc pass aligned with the From domain)"
    return None


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
            imap = aioimaplib.IMAP4_SSL(host=config["host"], port=config["port"])
        else:
            imap = aioimaplib.IMAP4(host=config["host"], port=config["port"])

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

            untrusted = sender_trust_problem(msg)
            if untrusted:
                logger.warning(
                    "email_dropped_untrusted_sender",
                    from_addr=from_addr,
                    subject=subject[:100],
                    reason=untrusted,
                )
                # Marked read so it is not re-evaluated (and does not crowd out
                # trusted mail in the 10-per-poll window) on every poll.
                with contextlib.suppress(Exception):
                    await imap.store(email_id, "+FLAGS", r"(\Seen)")
                continue

            # Extract body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")[:500]
                        break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")[:500]

            # Build goal from email
            goal_text = subject
            if body.strip():
                goal_text += f"\n\nAdditional context from email:\n{body.strip()}"

            # Identity independent of the \Seen flag: if marking the email read
            # below fails (network blip, connection drop) or two overlapping
            # poll cycles fetch the same still-UNSEEN email concurrently, this
            # is what stops the email being resubmitted as a brand-new goal on
            # the next poll — the \Seen flag alone isn't atomic with submission.
            dedup_key = _email_identity_key(msg, raw_email)
            _tenant_id = getattr(tenant_ctx, "tenant_id", None)
            tenant_key = _tenant_id if isinstance(_tenant_id, str) and _tenant_id else "imap"
            _wire_email_dedup_redis(goal_service)

            if await _email_dedup.get_existing(tenant_key, dedup_key):
                logger.info(
                    "email_already_processed_skip_duplicate",
                    from_addr=from_addr,
                    subject=subject[:100],
                )
                with contextlib.suppress(Exception):
                    await imap.store(email_id, "+FLAGS", r"(\Seen)")
                continue

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

                # Record the submission BEFORE attempting to mark \Seen: if the
                # store call fails and this email is re-fetched as still-UNSEEN
                # on the next poll, the dedup check above will catch it there.
                await _email_dedup.register(tenant_key, dedup_key, result["goal_id"])

                # Mark as read
                await imap.store(email_id, "+FLAGS", r"(\Seen)")
            except Exception as exc:
                logger.warning("email_goal_submission_failed", error=str(exc))

        await imap.logout()
    except Exception as exc:
        logger.warning("imap_check_failed", error=str(exc))

    return processed
