"""Async SSE (Server-Sent Events) streaming client using httpx."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from agentverse.models import GoalEvent


async def stream_sse(
    url: str,
    headers: dict[str, str],
    timeout: float | None = None,
) -> AsyncIterator[GoalEvent]:
    """Yield GoalEvent objects from an SSE endpoint.

    Args:
        url: Full SSE endpoint URL.
        headers: HTTP headers (must include X-API-Key).
        timeout: Optional overall wall-clock timeout in seconds.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                payload_str = raw_line[6:].strip()
                if not payload_str or payload_str == "[DONE]":
                    break
                try:
                    payload: dict[str, Any] = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                yield GoalEvent(
                    type=payload.get("type", "unknown"),
                    goal_id=payload.get("goal_id", ""),
                    ts=datetime.now(UTC),
                    data=payload,
                )
