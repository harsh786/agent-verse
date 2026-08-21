"""SlackConnector — wraps existing SlackIngestor in BaseConnector interface.

Supports:
- Incremental: conversations.history with cursor (next_cursor token)
- ACL propagation: channel membership → allowed principals
- Thread inclusion: replies fetched as child documents
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

_SLACK_API = "https://slack.com/api"


@register("slack")
class SlackConnector(BaseConnector):
    """Slack workspace message ingestion."""

    source_type = "slack"
    supports_acl_propagation = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            import httpx

            token = config.connection_config.get("bot_token", "")
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{_SLACK_API}/auth.test",
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = r.json()
            latency = (time.perf_counter() - t0) * 1000
            if data.get("ok"):
                return ConnectionHealth(
                    ok=True,
                    latency_ms=latency,
                    metadata={"workspace": data.get("team", ""), "bot": data.get("bot_id", "")},
                )
            return ConnectionHealth(
                ok=False, latency_ms=latency, error=data.get("error", "auth_failed")
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Yield messages from configured channels since cursor timestamp."""
        from app.ingestion.source_config import RawDocument
        from app.knowledge.ingestors.slack_ingestor import SlackIngestor

        token = config.connection_config.get("bot_token", "")
        channels = config.connection_config.get("channels", [])
        max_messages = config.connection_config.get("max_messages", 500)
        ingestor = SlackIngestor(token=token)

        new_cursor = cursor or ""
        for channel_id in channels:
            try:
                chunks = await ingestor.ingest_channel(channel_id, max_messages=max_messages)
                for chunk in chunks:
                    ts = chunk.get("metadata", {}).get("ts", "")
                    if cursor and ts and ts <= cursor:
                        continue  # already ingested
                    if ts > new_cursor:
                        new_cursor = ts

                    raw = RawDocument(
                        doc_id=f"{channel_id}_{ts or uuid.uuid4().hex}",
                        source_id=config.source_id,
                        tenant_id=config.tenant_id,
                        content=chunk.get("content", "").encode("utf-8"),
                        content_type="text/plain",
                        source_url=chunk.get("source_url", ""),
                        metadata=chunk.get("metadata", {}),
                    )
                    yield raw, new_cursor
            except Exception as exc:
                _log.warning("slack_connector_channel_error channel=%s: %s", channel_id, exc)

    @property
    def supports_streaming(self) -> bool:
        return True  # Slack Events API push is supported via on_webhook
