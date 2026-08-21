"""TeamsConnector — Microsoft Teams messages and channel conversations.

Uses Microsoft Graph API.
Cursor: last message createdDateTime per channel.
Supports teams, channels, and direct messages.
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

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


@register("teams", feature_flag="ingestion_connector_teams_enabled")
class TeamsConnector(BaseConnector):
    """Microsoft Teams connector — channels, threads, and DMs via Graph API."""

    source_type = "teams"
    supports_acl_propagation = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            import httpx

            token = await self._get_token(config)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{_GRAPH_BASE}/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                user = resp.json()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"user": user.get("displayName"), "email": user.get("mail")},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        import httpx

        from app.ingestion.source_config import RawDocument

        token = await self._get_token(config)
        team_id = config.connection_config.get("team_id", "")
        channel_ids = config.connection_config.get("channel_ids") or []

        headers = {"Authorization": f"Bearer {token}"}
        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            # If no specific channels, fetch all channels in the team
            if not channel_ids and team_id:
                r = await client.get(f"{_GRAPH_BASE}/teams/{team_id}/channels", headers=headers)
                if r.is_success:
                    channel_ids = [ch["id"] for ch in r.json().get("value", [])]

            for channel_id in channel_ids:
                url = f"{_GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages"
                if cursor:
                    url += f"?$filter=lastModifiedDateTime gt {cursor}"

                while url:
                    r = await client.get(url, headers=headers)
                    if not r.is_success:
                        _log.warning("teams: %s → %d", url, r.status_code)
                        break
                    data = r.json()
                    for msg in data.get("value", []):
                        body_content = msg.get("body", {}).get("content", "")
                        if not body_content or body_content == "<systemEventMessage/>":
                            continue
                        ts = msg.get("lastModifiedDateTime", "")
                        new_cursor = max(new_cursor, ts)
                        author = msg.get("from", {}).get("user", {}).get("displayName", "Unknown")
                        text = f"[Teams] {author}: {body_content}"
                        doc = RawDocument(
                            doc_id=str(uuid.uuid4()),
                            source_id=config.source_id,
                            tenant_id=config.tenant_id,
                            source_url=msg.get(
                                "webUrl", f"teams://{team_id}/{channel_id}/{msg.get('id')}"
                            ),
                            content=text.encode(),
                            content_type="text/plain",
                            metadata={"author": author, "channel": channel_id, "ts": ts},
                        )
                        yield doc, new_cursor
                    url = data.get("@odata.nextLink")

    async def _get_token(self, config: SourceConfig) -> str:
        """Acquire OAuth2 token via client credentials flow."""
        import httpx

        cc = config.connection_config
        tenant = cc.get("tenant_id", "")
        client_id = cc.get("client_id", "")
        client_secret = cc.get("client_secret", "")
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            r.raise_for_status()
            return r.json()["access_token"]
