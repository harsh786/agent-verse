"""Slack channel message ingestor via Web API."""
from __future__ import annotations
from typing import Any
import httpx
from app.observability.logging import get_logger

logger = get_logger(__name__)
_SLACK_API = "https://slack.com/api"


class SlackIngestor:
    def __init__(self, token: str) -> None:
        self._token = token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def ingest_channel(
        self, channel_id: str, *, channel_name: str = "", max_messages: int = 500
    ) -> list[dict[str, Any]]:
        chunks = []
        cursor = None
        message_count = 0

        while message_count < max_messages:
            params: dict = {"channel": channel_id, "limit": 200}
            if cursor:
                params["cursor"] = cursor

            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(f"{_SLACK_API}/conversations.history",
                               params=params, headers=self._headers())
                data = r.json()
                if not data.get("ok"):
                    logger.warning("slack_api_error", error=data.get("error"))
                    break

                messages = data.get("messages", [])
                # Group messages into context windows (~5 messages per chunk)
                window = []
                last_ts = ""
                for msg in messages:
                    if msg.get("type") != "message" or msg.get("subtype"):
                        continue
                    text = (msg.get("text") or "").strip()
                    if len(text) < 10:
                        continue
                    last_ts = msg.get("ts", "")
                    window.append(text)
                    message_count += 1
                    if len(window) >= 5 or message_count >= max_messages:
                        chunk_text = "\n".join(window)
                        chunks.append({
                            "content": chunk_text,
                            "source_url": f"https://slack.com/archives/{channel_id}",
                            "source_type": "slack",
                            "source_doc_id": f"{channel_id}/{last_ts}",
                            "page_number": None,
                            "metadata": {"channel_id": channel_id, "channel_name": channel_name},
                        })
                        window = []

                if window:
                    chunks.append({
                        "content": "\n".join(window),
                        "source_url": f"https://slack.com/archives/{channel_id}",
                        "source_type": "slack", "source_doc_id": channel_id,
                        "page_number": None, "metadata": {"channel_id": channel_id},
                    })

                response_meta = data.get("response_metadata", {})
                cursor = response_meta.get("next_cursor")
                if not cursor or message_count >= max_messages:
                    break

        return chunks
