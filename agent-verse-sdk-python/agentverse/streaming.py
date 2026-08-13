"""Async SSE (Server-Sent Events) streaming client using httpx."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from agentverse.models import CoordinationEvent, GoalEvent


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


async def stream_coordination_sse(
    url: str,
    headers: dict[str, str],
    *,
    after_sequence: int = 0,
    timeout: float | None = None,
) -> AsyncIterator[CoordinationEvent]:
    """Yield persisted coordination events once, resuming strictly after a cursor."""
    request_headers = dict(headers)
    request_headers["Last-Event-ID"] = str(after_sequence)
    seen: set[str] = set()
    cursor = after_sequence
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        async with client.stream("GET", url, headers=request_headers) as response:
            response.raise_for_status()
            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                payload_text = raw_line[6:].strip()
                if not payload_text or payload_text == "[DONE]":
                    break
                try:
                    event = CoordinationEvent.model_validate_json(payload_text)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError("malformed coordination SSE event") from exc
                if event.event_id in seen or event.sequence <= cursor:
                    continue
                seen.add(event.event_id)
                cursor = event.sequence
                yield event
