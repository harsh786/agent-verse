"""Slack channel adapter — Bolt-compatible event handling.

Handles: slash commands, @mention commands, app_home, interactive components.
Setup: SLACK_BOT_TOKEN + SLACK_SIGNING_SECRET env vars.
Webhook: POST /v1/gateway/{org_id}/slack/events
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from typing import Any

import httpx
import structlog
from opentelemetry import trace

from app.gateway.channels.base import ChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse, ResponseAction

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


class SlackChannelAdapter(ChannelAdapter):
    """Slack channel adapter — handles slash commands and @mention events."""

    channel_name = "slack"

    def __init__(
        self,
        bot_token: str | None = None,
        signing_secret: str | None = None,
    ) -> None:
        self._token = bot_token or os.getenv("SLACK_BOT_TOKEN", "")
        self._signing_secret = signing_secret or os.getenv("SLACK_SIGNING_SECRET", "")

    async def normalize(
        self, raw_payload: dict[str, Any], tenant_id: str, org_id: str,
    ) -> OrgCommand:
        with _tracer.start_as_current_span("slack.normalize"):
            command_id = str(uuid.uuid4())
            event_type = raw_payload.get("type", "")

            # Slash command
            if "command" in raw_payload:
                text = f"{raw_payload['command']} {raw_payload.get('text', '')}".strip()
                return OrgCommand(
                    command_id=command_id,
                    tenant_id=tenant_id,
                    org_id=org_id,
                    text=text,
                    actor_id=raw_payload.get("user_id", ""),
                    actor_name=raw_payload.get("user_name", ""),
                    actor_channel="slack",
                    conversation_id=raw_payload.get("channel_id", ""),
                    raw_payload=raw_payload,
                )

            # App mention
            if event_type == "event_callback":
                event = raw_payload.get("event", {})
                if event.get("type") in ("app_mention", "message"):
                    text = event.get("text", "").strip()
                    # Strip bot mention prefix <@BOTID>
                    if text.startswith("<@"):
                        text = text.split(">", 1)[-1].strip()
                    return OrgCommand(
                        command_id=command_id,
                        tenant_id=tenant_id,
                        org_id=org_id,
                        text=text,
                        actor_id=event.get("user", ""),
                        actor_channel="slack",
                        conversation_id=event.get("channel", ""),
                        raw_payload=raw_payload,
                    )

            return OrgCommand(
                command_id=command_id,
                tenant_id=tenant_id,
                org_id=org_id,
                text="",
                actor_channel="slack",
                raw_payload=raw_payload,
            )

    def format_response(self, response: OrgResponse) -> dict[str, Any]:
        """Format as Slack Block Kit message."""
        blocks: list[dict[str, Any]] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": response.text[:3000]}},
        ]
        if response.actions:
            elements: list[dict[str, Any]] = []
            for action in response.actions[:5]:
                elements.append({
                    "type": "button",
                    "text": {"type": "plain_text", "text": action.label},
                    "action_id": action.action_id,
                    "value": action.action_id,
                    "style": "primary" if action.action_type == "approve" else "default",
                })
            blocks.append({"type": "actions", "elements": elements})
        return {"blocks": blocks, "text": response.text[:150]}

    async def post_message(
        self, channel: str, response: OrgResponse,
    ) -> dict[str, Any] | None:
        if not self._token:
            return None
        payload = self.format_response(response)
        payload["channel"] = channel
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                return r.json()
        except Exception as exc:
            _log.warning("slack.post_message.failed", error=str(exc))
            return None

    async def verify_auth(
        self, request_headers: dict[str, str], raw_payload: dict[str, Any],
    ) -> bool:
        if not self._signing_secret:
            return True
        timestamp = request_headers.get("x-slack-request-timestamp", "")
        signature = request_headers.get("x-slack-signature", "")
        body = str(raw_payload)
        sig_base = f"v0:{timestamp}:{body}"
        computed = "v0=" + hmac.new(
            self._signing_secret.encode(),
            sig_base.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(computed, signature)
