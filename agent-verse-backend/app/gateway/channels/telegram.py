"""Telegram channel adapter — full Bot API integration.

Handles:
  - Text commands → OrgCommand
  - Documents/photos → OrgCommand with files
  - Callback queries (inline button presses) → action execution
  - Outbound responses with inline keyboards
  - /start, /status, /approve, /brief slash commands
  - Voice messages (transcribed externally before reaching this adapter)

Setup: TELEGRAM_BOT_TOKEN env var must be set.
Webhook URL: POST /v1/gateway/{org_id}/telegram/webhook
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
from app.gateway.command import CommandFile, OrgCommand, OrgResponse

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot"


class TelegramChannelAdapter(ChannelAdapter):
    """Full Telegram Bot API integration."""

    channel_name = "telegram"

    def __init__(self, bot_token: str | None = None) -> None:
        self._token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._api_base = f"{TELEGRAM_API_BASE}{self._token}"

    async def normalize(
        self,
        raw_payload: dict[str, Any],
        tenant_id: str,
        org_id: str,
    ) -> OrgCommand:
        with _tracer.start_as_current_span("telegram.normalize") as span:
            update = raw_payload
            command_id = str(uuid.uuid4())

            # Message
            if message := update.get("message"):
                chat_id = str(message.get("chat", {}).get("id", ""))
                user = message.get("from", {})
                actor_id = str(user.get("id", ""))
                actor_name = (
                    f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                    or user.get("username", "")
                )

                text = message.get("text", "") or message.get("caption", "") or ""
                files: list[CommandFile] = []

                # Handle file attachments
                if doc := message.get("document"):
                    files.append(
                        CommandFile(
                            filename=doc.get("file_name", "file"),
                            content_type=doc.get("mime_type", "application/octet-stream"),
                            url=await self._get_file_url(doc.get("file_id", "")),
                        )
                    )
                elif photos := message.get("photo"):
                    # Largest photo
                    photo = max(photos, key=lambda p: p.get("file_size", 0))
                    files.append(
                        CommandFile(
                            filename="photo.jpg",
                            content_type="image/jpeg",
                            url=await self._get_file_url(photo.get("file_id", "")),
                        )
                    )

                span.set_attribute("chat_id", chat_id)
                span.set_attribute("text_length", len(text))
                return OrgCommand(
                    command_id=command_id,
                    tenant_id=tenant_id,
                    org_id=org_id,
                    text=text or "(attachment)",
                    actor_id=actor_id,
                    actor_name=actor_name,
                    actor_channel="telegram",
                    conversation_id=chat_id,
                    files=files,
                    raw_payload=raw_payload,
                )

            # Callback query (inline button press)
            if cq := update.get("callback_query"):
                user = cq.get("from", {})
                actor_id = str(user.get("id", ""))
                chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
                return OrgCommand(
                    command_id=command_id,
                    tenant_id=tenant_id,
                    org_id=org_id,
                    text=f"/callback {cq.get('data', '')}",
                    actor_id=actor_id,
                    actor_channel="telegram",
                    conversation_id=chat_id,
                    raw_payload=raw_payload,
                )

            # Fallback
            return OrgCommand(
                command_id=command_id,
                tenant_id=tenant_id,
                org_id=org_id,
                text="",
                actor_channel="telegram",
                raw_payload=raw_payload,
            )

    def format_response(self, response: OrgResponse) -> dict[str, Any]:
        """Format OrgResponse for Telegram (text + optional inline keyboard)."""
        buttons: list[list[dict[str, str]]] = []
        if response.actions:
            row: list[dict[str, str]] = []
            for action in response.actions[:4]:  # Telegram max 4 per row
                emoji = {"approve": "✅", "reject": "❌", "view": "📋", "ask": "❓"}.get(
                    action.action_type, "🔹"
                )
                row.append(
                    {
                        "text": f"{emoji} {action.label}",
                        "callback_data": action.action_id,
                    }
                )
            buttons.append(row)

        payload: dict[str, Any] = {
            "text": response.text[:4096],  # Telegram message limit
            "parse_mode": "HTML",
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return payload

    async def send_message(
        self,
        chat_id: str,
        response: OrgResponse,
    ) -> dict[str, Any] | None:
        """Send formatted response to a Telegram chat."""
        if not self._token:
            _log.warning("telegram.send_message.no_token")
            return None
        payload = self.format_response(response)
        payload["chat_id"] = chat_id
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(f"{self._api_base}/sendMessage", json=payload)
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            _log.warning("telegram.send_message.failed", error=str(exc))
            return None

    async def _get_file_url(self, file_id: str) -> str | None:
        """Get download URL for a Telegram file_id."""
        if not self._token or not file_id:
            return None
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self._api_base}/getFile", params={"file_id": file_id})
                data = r.json()
                path = data.get("result", {}).get("file_path", "")
                if path:
                    return f"https://api.telegram.org/file/bot{self._token}/{path}"
        except Exception:
            pass
        return None

    async def verify_auth(
        self,
        request_headers: dict[str, str],
        raw_payload: dict[str, Any],
        raw_body: bytes | None = None,
    ) -> bool:
        """Verify Telegram webhook secret token header.

        ``raw_body`` is unused here — Telegram authenticates via a static
        secret token header (``X-Telegram-Bot-Api-Secret-Token``), not an
        HMAC over the request body, so there's nothing to verify it against.
        Accepted for signature-compatibility with the shared dispatch call
        site in ``app/gateway/router.py``.
        """
        secret_token = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
        if not secret_token:
            return True  # no secret configured, allow all
        header_token = request_headers.get("x-telegram-bot-api-secret-token", "")
        return hmac.compare_digest(secret_token, header_token)
