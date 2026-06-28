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
        self._db: Any = None

    def set_db(self, db_factory: Any) -> None:
        """Wire in async SQLAlchemy session factory (called during lifespan)."""
        self._db = db_factory

    async def sync_from_db(self, tenant_id: str | None = None) -> None:
        """Load persisted channels from DB into the in-memory cache."""
        if self._db is None:
            return
        try:
            from sqlalchemy import text as _t
            async with self._db() as session:
                if tenant_id:
                    await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
                query = (
                    "SELECT channel_id, tenant_id, channel_type, config, enabled"
                    " FROM notification_channels"
                )
                params: dict[str, Any] = {}
                if tenant_id:
                    query += " WHERE tenant_id = :tid"
                    params["tid"] = tenant_id
                result = await session.execute(_t(query), params)
                for row in result.fetchall():
                    ch = NotificationChannel(
                        channel_id=row[0],
                        tenant_id=row[1],
                        channel_type=row[2],
                        config=row[3] or {},
                        enabled=row[4],
                    )
                    self._channels.setdefault(row[1], [])
                    if not any(c.channel_id == ch.channel_id for c in self._channels[row[1]]):
                        self._channels[row[1]].append(ch)
        except Exception as exc:
            logger.warning("notification_sync_failed", error=str(exc))

    def add_channel(self, channel: NotificationChannel) -> None:
        self._channels.setdefault(channel.tenant_id, []).append(channel)
        if self._db is not None:
            import asyncio
            asyncio.create_task(self._persist_channel(channel))  # noqa: RUF006

    async def _persist_channel(self, channel: NotificationChannel) -> None:
        """Persist a channel to the DB (fire-and-forget)."""
        try:
            import json as _json

            from sqlalchemy import text as _t
            async with self._db() as session, session.begin():
                await session.execute(_t("""
                    INSERT INTO notification_channels
                        (channel_id, tenant_id, channel_type, config, enabled)
                    VALUES (:cid, :tid, :ctype, :cfg::jsonb, :enabled)
                    ON CONFLICT (channel_id) DO UPDATE
                        SET config = EXCLUDED.config, enabled = EXCLUDED.enabled
                """), {
                    "cid": channel.channel_id,
                    "tid": channel.tenant_id,
                    "ctype": channel.channel_type,
                    "cfg": _json.dumps(channel.config),
                    "enabled": channel.enabled,
                })
        except Exception as exc:
            logger.warning("notification_persist_failed", error=str(exc))

    def get_channels(self, tenant_id: str) -> list[NotificationChannel]:
        return [c for c in self._channels.get(tenant_id, []) if c.enabled]

    def remove_channel(self, channel_id: str, tenant_id: str) -> bool:
        channels = self._channels.get(tenant_id, [])
        before = len(channels)
        self._channels[tenant_id] = [c for c in channels if c.channel_id != channel_id]
        removed = len(self._channels[tenant_id]) < before
        if removed and self._db is not None:
            import asyncio
            asyncio.create_task(self._delete_channel(channel_id))  # noqa: RUF006
        return removed

    async def _delete_channel(self, channel_id: str) -> None:
        """Remove a channel from the DB (fire-and-forget)."""
        try:
            from sqlalchemy import text as _t
            async with self._db() as session, session.begin():
                await session.execute(
                    _t("DELETE FROM notification_channels WHERE channel_id = :cid"),
                    {"cid": channel_id},
                )
        except Exception as exc:
            logger.warning("notification_delete_failed", error=str(exc))

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
