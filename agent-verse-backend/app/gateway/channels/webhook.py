"""Generic HMAC-signed webhook adapter.

Any external system can POST to /v1/gateway/{org_id}/webhook
with a valid HMAC-SHA256 signature to trigger org commands.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from typing import Any

import structlog
from opentelemetry import trace

from app.gateway.channels.base import ChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


class WebhookChannelAdapter(ChannelAdapter):
    """Generic HMAC-signed webhook receiver."""

    channel_name = "webhook"

    def __init__(self, webhook_secret: str | None = None) -> None:
        self._secret = webhook_secret or os.getenv("WEBHOOK_SECRET", "")

    async def normalize(
        self,
        raw_payload: dict[str, Any],
        tenant_id: str,
        org_id: str,
    ) -> OrgCommand:
        with _tracer.start_as_current_span("webhook.normalize"):
            command_id = str(uuid.uuid4())
            # Support both "command" field and trigger-based inference
            text = (
                raw_payload.get("command")
                or raw_payload.get("text")
                or raw_payload.get("message")
                or _infer_command_from_trigger(raw_payload)
            )
            return OrgCommand(
                command_id=command_id,
                tenant_id=tenant_id,
                org_id=org_id,
                text=str(text),
                actor_id=raw_payload.get("actor_id", "webhook"),
                actor_channel="webhook",
                conversation_id=raw_payload.get("conversation_id"),
                urgency=raw_payload.get("urgency", "normal"),
                raw_payload=raw_payload,
            )

    def format_response(self, response: OrgResponse) -> dict[str, Any]:
        return {
            "command_id": response.command_id,
            "text": response.text,
            "status": response.status,
            "mission_id": response.mission_id,
            "requires_action": response.requires_action,
        }

    async def verify_auth(
        self,
        request_headers: dict[str, str],
        raw_payload: dict[str, Any],
    ) -> bool:
        if not self._secret:
            return True
        signature = request_headers.get("x-webhook-signature", "")
        import json as _json

        body = _json.dumps(raw_payload, separators=(",", ":")).encode()
        computed = (
            "sha256="
            + hmac.new(
                self._secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(computed, signature)


def _infer_command_from_trigger(payload: dict[str, Any]) -> str:
    """Infer a natural-language command from a webhook trigger event."""
    trigger = payload.get("trigger", "")
    mapping = {
        "github.pr_merged": "Analyse the merged pull request and check deployment readiness",
        "github.issue_opened": "Triage the new GitHub issue",
        "crm.deal_closed": "Start customer onboarding process for the closed deal",
        "calendar.meeting_ended": "Summarise the meeting and assign action items",
        "alert.critical": "Respond to the critical production alert",
    }
    return mapping.get(trigger, f"Handle event: {trigger or 'unknown'}")
