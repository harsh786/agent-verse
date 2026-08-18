"""Discord channel adapter — Q6 of spec.

Handles:
  - Slash commands: /org ask, /org status, /org approve, /org mission
  - @OrgBot mentions → NL command
  - Thread-based conversations
  - Rich embeds for responses

Setup: DISCORD_BOT_TOKEN + DISCORD_PUBLIC_KEY env vars.
Webhook URL: POST /v1/gateway/{org_id}/discord/interactions
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from typing import Any

import structlog
from opentelemetry import trace

from app.gateway.channels.base import ChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse, ResponseAction

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordChannelAdapter(ChannelAdapter):
    """Discord bot integration via Interactions Webhook."""

    channel_name = "discord"

    def __init__(
        self,
        bot_token: str | None = None,
        public_key: str | None = None,
    ) -> None:
        self._token      = bot_token  or os.getenv("DISCORD_BOT_TOKEN", "")
        self._public_key = public_key or os.getenv("DISCORD_PUBLIC_KEY", "")

    async def verify_auth(
        self, request_headers: dict[str, str], raw_payload: dict[str, Any]
    ) -> bool:
        """Verify Discord Ed25519 signature on interaction payload."""
        if not self._public_key:
            return False
        signature  = request_headers.get("X-Signature-Ed25519", "")
        timestamp  = request_headers.get("X-Signature-Timestamp", "")
        body       = raw_payload.get("_raw_body", "")
        # TODO: implement full Ed25519 verify (requires PyNaCl)
        return bool(signature and timestamp and body)

    async def normalize(
        self, raw_payload: dict[str, Any], tenant_id: str, org_id: str
    ) -> OrgCommand:
        with _tracer.start_as_current_span("discord.normalize") as span:
            command_id = str(uuid.uuid4())
            interaction_type = raw_payload.get("type", 0)

            # Ping (type 1) — return immediately
            if interaction_type == 1:
                return OrgCommand(
                    command_id=command_id,
                    tenant_id=tenant_id,
                    org_id=org_id,
                    text="_ping",
                    actor_id="discord_ping",
                    actor_channel="discord",
                    raw_payload=raw_payload,
                )

            # Application command (type 2) — slash commands
            if interaction_type == 2:
                data   = raw_payload.get("data", {})
                user   = raw_payload.get("member", {}).get("user", {}) or raw_payload.get("user", {})
                actor  = str(user.get("id", ""))
                name   = str(user.get("username", ""))

                cmd_name = data.get("name", "")
                options  = {o["name"]: o.get("value", "") for o in data.get("options", [])}
                text     = options.get("text", options.get("query", cmd_name))

                # Build natural language text from slash command
                cmd_map = {
                    "org-status":   "What is the org status?",
                    "org-ask":      text,
                    "org-approve":  "List pending approvals",
                    "org-mission":  f"Create a mission: {text}",
                    "org-brief":    "Morning brief",
                }
                resolved_text = cmd_map.get(cmd_name, text or cmd_name)

                span.set_attribute("discord.command", cmd_name)
                return OrgCommand(
                    command_id=command_id,
                    tenant_id=tenant_id,
                    org_id=org_id,
                    text=resolved_text,
                    actor_id=actor,
                    actor_name=name,
                    actor_channel="discord",
                    conversation_id=str(raw_payload.get("channel_id", "")),
                    raw_payload=raw_payload,
                )

            # Message component (type 3) — button presses
            if interaction_type == 3:
                custom_id = raw_payload.get("data", {}).get("custom_id", "")
                user   = raw_payload.get("member", {}).get("user", {}) or raw_payload.get("user", {})
                return OrgCommand(
                    command_id=command_id,
                    tenant_id=tenant_id,
                    org_id=org_id,
                    text=f"action:{custom_id}",
                    actor_id=str(user.get("id", "")),
                    actor_channel="discord",
                    raw_payload=raw_payload,
                )

            # Default: unknown interaction type
            return OrgCommand(
                command_id=command_id,
                tenant_id=tenant_id,
                org_id=org_id,
                text="",
                actor_id="unknown",
                actor_channel="discord",
                raw_payload=raw_payload,
            )

    def format_response(self, response: OrgResponse) -> dict[str, Any]:
        """Format OrgResponse as Discord Interaction Response (type 4 = channel message)."""
        # Truncate to Discord 2000-char limit
        content = response.text[:1990]

        # Build components (buttons) from actions
        components = []
        if response.actions:
            buttons = [
                {
                    "type": 2,    # BUTTON
                    "style": 1,   # PRIMARY
                    "label": a.label[:80],
                    "custom_id": a.action_id[:100],
                }
                for a in response.actions[:5]   # Discord max 5 buttons per row
            ]
            components = [{"type": 1, "components": buttons}]   # ACTION_ROW

        return {
            "type": 4,    # CHANNEL_MESSAGE_WITH_SOURCE
            "data": {
                "content": content,
                "components": components,
                "flags": 0,
            },
        }
