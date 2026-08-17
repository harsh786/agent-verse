"""DiscordConnector — Discord server message ingestion.

Uses discord.py HTTP client (no bot required for basic read access with token).
Cursor: last message snowflake ID per channel.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)
_DISCORD_BASE = "https://discord.com/api/v10"


@register("discord", feature_flag="ingestion_connector_discord_enabled")
class DiscordConnector(BaseConnector):
    """Discord connector — server channels via bot token."""

    source_type = "discord"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        import httpx
        t0 = time.perf_counter()
        try:
            token = config.connection_config.get("bot_token", "")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{_DISCORD_BASE}/users/@me",
                    headers={"Authorization": f"Bot {token}"},
                )
                r.raise_for_status()
                user = r.json()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True, latency_ms=latency,
                metadata={"bot": user.get("username"), "id": user.get("id")},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        import httpx

        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        token = cc.get("bot_token", "")
        channel_ids = cc.get("channel_ids") or []
        batch_size = min(int(cc.get("batch_size", 100)), 100)

        headers = {"Authorization": f"Bot {token}"}
        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            for channel_id in channel_ids:
                params: dict = {"limit": batch_size}
                if cursor:
                    params["after"] = cursor  # snowflake ID — after this ID

                while True:
                    r = await client.get(
                        f"{_DISCORD_BASE}/channels/{channel_id}/messages",
                        params=params,
                        headers=headers,
                    )
                    if not r.is_success:
                        _log.warning("discord: channel %s → %d", channel_id, r.status_code)
                        break
                    messages = r.json()
                    if not messages:
                        break

                    for msg in reversed(messages):  # oldest first
                        msg_id = msg.get("id", "")
                        if not msg_id:
                            continue
                        new_cursor = max(new_cursor, msg_id)
                        author = msg.get("author", {}).get("username", "Unknown")
                        content = msg.get("content", "")
                        ts = msg.get("timestamp", "")
                        if not content.strip():
                            continue
                        text = f"[Discord] {author}: {content}"
                        doc = RawDocument(
                            doc_id=str(uuid.uuid4()),
                            source_id=config.source_id,
                            tenant_id=config.tenant_id,
                            source_url=f"https://discord.com/channels/{cc.get('guild_id', '_')}/{channel_id}/{msg_id}",
                            content=text.encode(),
                            content_type="text/plain",
                            metadata={"author": author, "channel": channel_id, "ts": ts, "msg_id": msg_id},
                        )
                        yield doc, new_cursor

                    # Use last message ID for pagination
                    if len(messages) < batch_size:
                        break
                    params = {"limit": batch_size, "after": messages[0].get("id", "")}
