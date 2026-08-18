"""WhatsApp Business Cloud API adapter.

Setup: WHATSAPP_ACCESS_TOKEN + WHATSAPP_PHONE_NUMBER_ID env vars.
Webhook: POST /v1/gateway/{org_id}/whatsapp/webhook
"""
from __future__ import annotations

import hmac
import hashlib
import os
import uuid
from typing import Any

import httpx
import structlog
from opentelemetry import trace

from app.gateway.channels.base import ChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

_GRAPH_API = "https://graph.facebook.com/v19.0"


class WhatsAppChannelAdapter(ChannelAdapter):
    """WhatsApp Business Cloud API adapter."""

    channel_name = "whatsapp"

    def __init__(self) -> None:
        self._token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        self._phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        self._app_secret = os.getenv("WHATSAPP_APP_SECRET", "")

    async def normalize(
        self, raw_payload: dict[str, Any], tenant_id: str, org_id: str,
    ) -> OrgCommand:
        with _tracer.start_as_current_span("whatsapp.normalize"):
            command_id = str(uuid.uuid4())
            try:
                entry = raw_payload.get("entry", [{}])[0]
                change = entry.get("changes", [{}])[0]
                value = change.get("value", {})
                messages = value.get("messages", [])
                contacts = value.get("contacts", [{}])

                if messages:
                    msg = messages[0]
                    from_number = msg.get("from", "")
                    actor_name = contacts[0].get("profile", {}).get("name", "") if contacts else ""
                    text = msg.get("text", {}).get("body", "") if msg.get("type") == "text" else ""
                    return OrgCommand(
                        command_id=command_id,
                        tenant_id=tenant_id,
                        org_id=org_id,
                        text=text,
                        actor_id=from_number,
                        actor_name=actor_name,
                        actor_channel="whatsapp",
                        conversation_id=from_number,
                        raw_payload=raw_payload,
                    )
            except Exception as exc:
                _log.warning("whatsapp.normalize.failed", error=str(exc))

            return OrgCommand(
                command_id=command_id, tenant_id=tenant_id, org_id=org_id,
                text="", actor_channel="whatsapp", raw_payload=raw_payload,
            )

    def format_response(self, response: OrgResponse) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "type": "text",
            "text": {"body": response.text[:4096]},
        }

    async def send_message(self, to: str, response: OrgResponse) -> dict[str, Any] | None:
        if not self._token or not self._phone_id:
            return None
        payload = self.format_response(response)
        payload["to"] = to
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{_GRAPH_API}/{self._phone_id}/messages",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                return r.json()
        except Exception as exc:
            _log.warning("whatsapp.send_message.failed", error=str(exc))
            return None

    async def verify_auth(
        self, request_headers: dict[str, str], raw_payload: dict[str, Any],
    ) -> bool:
        if not self._app_secret:
            return True
        signature = request_headers.get("x-hub-signature-256", "")
        import json as _json  # noqa: PLC0415
        body = _json.dumps(raw_payload, separators=(",", ":"))
        computed = "sha256=" + hmac.new(
            self._app_secret.encode(), body.encode(), hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(computed, signature)
