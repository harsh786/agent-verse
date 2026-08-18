"""Microsoft Teams Bot Framework adapter (via Azure Bot Service).

Setup: TEAMS_APP_ID + TEAMS_APP_PASSWORD env vars.
Webhook: POST /v1/gateway/{org_id}/teams/messages
"""
from __future__ import annotations

import hmac
import os
import uuid
from typing import Any

import httpx
import structlog
from opentelemetry import trace

from app.gateway.channels.base import ChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse, ResponseAction

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


class MicrosoftTeamsAdapter(ChannelAdapter):
    """Microsoft Teams Bot Framework integration with Adaptive Cards."""

    channel_name = "teams"

    def __init__(self) -> None:
        self._app_id = os.getenv("TEAMS_APP_ID", "")
        self._app_password = os.getenv("TEAMS_APP_PASSWORD", "")
        self._token: str | None = None

    async def normalize(
        self, raw_payload: dict[str, Any], tenant_id: str, org_id: str,
    ) -> OrgCommand:
        with _tracer.start_as_current_span("teams.normalize"):
            command_id = str(uuid.uuid4())
            try:
                activity_type = raw_payload.get("type", "")
                if activity_type == "message":
                    text = raw_payload.get("text", "").strip()
                    from_obj = raw_payload.get("from", {})
                    service_url = raw_payload.get("serviceUrl", "")
                    conversation = raw_payload.get("conversation", {})
                    return OrgCommand(
                        command_id=command_id,
                        tenant_id=tenant_id,
                        org_id=org_id,
                        text=text,
                        actor_id=from_obj.get("id", ""),
                        actor_name=from_obj.get("name", ""),
                        actor_channel="teams",
                        conversation_id=conversation.get("id", ""),
                        raw_payload=raw_payload,
                    )
            except Exception as exc:
                _log.warning("teams.normalize.failed", error=str(exc))

            return OrgCommand(
                command_id=command_id, tenant_id=tenant_id, org_id=org_id,
                text="", actor_channel="teams", raw_payload=raw_payload,
            )

    def format_response(self, response: OrgResponse) -> dict[str, Any]:
        """Format as Adaptive Card activity."""
        card_body: list[dict[str, Any]] = [
            {"type": "TextBlock", "text": response.text, "wrap": True}
        ]
        actions: list[dict[str, Any]] = []
        for action in response.actions[:6]:
            style = "positive" if action.action_type == "approve" else "default"
            actions.append({
                "type": "Action.Submit",
                "title": action.label,
                "style": style,
                "data": {"action_id": action.action_id},
            })

        card: dict[str, Any] = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": card_body,
        }
        if actions:
            card["actions"] = actions

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }
            ],
        }

    async def verify_auth(
        self, request_headers: dict[str, str], raw_payload: dict[str, Any],
    ) -> bool:
        # Bot Framework uses JWT token validation — simplified header check
        auth = request_headers.get("authorization", "")
        return auth.startswith("Bearer ") and len(auth) > 20
