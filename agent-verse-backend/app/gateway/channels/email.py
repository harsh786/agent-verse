"""Email channel adapter — Q6 of spec.

Handles:
  - Inbound commands via email (subject = command or mission title)
  - Body = detailed instructions
  - Attachments → processed via OCR/parsers
  - Reply threading (conversation continuity)
  - Outbound responses as HTML email

Setup:
  EMAIL_IMAP_HOST, EMAIL_IMAP_PORT, EMAIL_IMAP_USER, EMAIL_IMAP_PASSWORD
  EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, EMAIL_SMTP_USER, EMAIL_SMTP_PASSWORD

Webhook URL: POST /v1/gateway/{org_id}/email/inbound
(via email provider inbound parse, e.g. SendGrid, Mailgun, Postmark)
"""

from __future__ import annotations

import html
import os
import re
import uuid
from typing import Any

import structlog
from opentelemetry import trace

from app.gateway.channels.base import ChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


class EmailChannelAdapter(ChannelAdapter):
    """Email inbound command parser — org@commands.agentverse.io."""

    channel_name = "email"

    def __init__(
        self,
        allowed_senders: list[str] | None = None,
        command_email: str | None = None,
    ) -> None:
        self._allowed_senders = set(allowed_senders or [])
        self._command_email = command_email or os.getenv("ORG_COMMAND_EMAIL", "")

    async def verify_auth(
        self, request_headers: dict[str, str], raw_payload: dict[str, Any]
    ) -> bool:
        """Verify sender is in allowed list."""
        sender = raw_payload.get("from", "")
        sender_email = self._extract_email(sender)
        if not self._allowed_senders:
            return True  # no whitelist = open
        return sender_email.lower() in {s.lower() for s in self._allowed_senders}

    async def normalize(
        self, raw_payload: dict[str, Any], tenant_id: str, org_id: str
    ) -> OrgCommand:
        with _tracer.start_as_current_span("email.normalize") as span:
            command_id = str(uuid.uuid4())
            sender = raw_payload.get("from", "")
            actor_email = self._extract_email(sender)
            subject = raw_payload.get("subject", "").strip()
            body_text = raw_payload.get("text", raw_payload.get("body", "")).strip()
            in_reply_to = raw_payload.get("in_reply_to", "")

            # Subject is the command; body adds context
            text = subject or body_text[:500]
            if body_text and subject:
                text = f"{subject}\n\n{body_text[:2000]}"

            conversation_id = self._extract_message_id(in_reply_to) if in_reply_to else None

            span.set_attribute("email.from", actor_email)
            span.set_attribute("email.subject_len", len(subject))

            return OrgCommand(
                command_id=command_id,
                tenant_id=tenant_id,
                org_id=org_id,
                text=text,
                actor_id=actor_email,
                actor_name=self._extract_name(sender),
                actor_channel="email",
                conversation_id=conversation_id,
                raw_payload=raw_payload,
            )

    def format_response(self, response: OrgResponse) -> dict[str, Any]:
        """Format OrgResponse as an email payload."""
        html_body = self._text_to_html(response.text)

        # Append action links if any
        if response.actions:
            html_body += "<p><strong>Actions:</strong></p><ul>"
            for action in response.actions:
                html_body += f"<li>{html.escape(action.label)}</li>"
            html_body += "</ul>"

        return {
            "subject": f"Re: AgentVerse — {response.text[:60]}…",
            "html_body": html_body,
            "text_body": response.text,
            "references": response.command_id,
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_email(sender: str) -> str:
        match = re.search(r"<([^>]+)>", sender)
        return match.group(1) if match else sender.strip()

    @staticmethod
    def _extract_name(sender: str) -> str | None:
        match = re.match(r"^([^<]+)<", sender)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_message_id(value: str) -> str | None:
        match = re.search(r"<([^>]+)>", value)
        return match.group(1) if match else None

    @staticmethod
    def _text_to_html(text: str) -> str:
        escaped = html.escape(text)
        return "<p>" + escaped.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
