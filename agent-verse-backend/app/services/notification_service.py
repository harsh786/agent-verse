"""Notification service — sends alerts when HITL approval is required.

Supports Slack webhooks and generic HTTP webhooks out of the box.
Designed to be extended with email, PagerDuty, etc.
Uses only open-source libraries (httpx).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class NotificationChannel:
    channel_id: str
    tenant_id: str
    channel_type: str   # "slack" | "webhook" | "teams"
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class NotificationService:
    """Dispatches notifications when approval is required or goals complete.

    Open source only — uses httpx for all HTTP calls.
    """

    def __init__(self) -> None:
        self._channels: dict[str, list[NotificationChannel]] = {}

    def add_channel(self, channel: NotificationChannel) -> None:
        self._channels.setdefault(channel.tenant_id, []).append(channel)

    def get_channels(self, tenant_id: str) -> list[NotificationChannel]:
        return [c for c in self._channels.get(tenant_id, []) if c.enabled]

    def remove_channel(self, channel_id: str, tenant_id: str) -> bool:
        channels = self._channels.get(tenant_id, [])
        before = len(channels)
        self._channels[tenant_id] = [c for c in channels if c.channel_id != channel_id]
        return len(self._channels[tenant_id]) < before

    async def notify_approval_required(
        self, *, request_id: str, goal_id: str, action: str,
        risk_level: str, tenant_id: str,
    ) -> dict[str, Any]:
        """Send notification to all tenant channels."""
        channels = self.get_channels(tenant_id)
        if not channels:
            return {"sent": 0, "channels": []}

        message = {
            "type": "approval_required",
            "request_id": request_id,
            "goal_id": goal_id,
            "action": action,
            "risk_level": risk_level,
            "text": (
                f"\u26a0\ufe0f *Approval Required*\n"
                f"Goal: `{goal_id}`\n"
                f"Action: `{action}`\n"
                f"Risk: `{risk_level}`\n"
                f"Request ID: `{request_id}`"
            ),
        }

        results = []
        for channel in channels:
            try:
                await self._send(channel, message)
                results.append({"channel_id": channel.channel_id, "status": "sent"})
            except Exception as exc:
                logger.warning("notification_failed",
                               channel_id=channel.channel_id, error=str(exc))
                results.append({"channel_id": channel.channel_id, "status": "failed",
                                "error": str(exc)})

        return {"sent": sum(1 for r in results if r["status"] == "sent"), "channels": results}

    async def notify_goal_complete(
        self, *, goal_id: str, status: str, tenant_id: str
    ) -> None:
        """Notify when a goal reaches a terminal state."""
        channels = self.get_channels(tenant_id)
        message = {
            "type": "goal_terminal",
            "goal_id": goal_id,
            "status": status,
            "text": f"{'✅' if status == 'complete' else '❌'} Goal `{goal_id}` {status}",
        }
        for channel in channels:
            try:
                await self._send(channel, message)
            except Exception as exc:
                logger.warning("goal_notification_failed", error=str(exc))

    async def _send(self, channel: NotificationChannel, message: dict[str, Any]) -> None:
        if channel.channel_type == "slack":
            webhook_url = channel.config.get("webhook_url", "")
            if webhook_url:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        webhook_url,
                        json={"text": message.get("text", json.dumps(message))}
                    )
                    resp.raise_for_status()
        elif channel.channel_type in {"webhook", "teams"}:
            url = channel.config.get("url", "")
            if url:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=message)
                    resp.raise_for_status()
