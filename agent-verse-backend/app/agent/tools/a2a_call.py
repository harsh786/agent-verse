"""Outbound A2A call tool — lets agents call external A2A/MCP agents as tools."""
from __future__ import annotations

from typing import Any

import httpx

from app.net.ssrf_guard import SSRFError, assert_public_url
from app.observability.logging import get_logger

logger = get_logger(__name__)


async def call_external_a2a_agent(
    *,
    agent_endpoint: str,
    task_description: str,
    context: dict[str, Any] | None = None,
    auth_token: str = "",
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Call an external A2A-compatible agent and wait for the result.

    Args:
        agent_endpoint: The A2A endpoint URL.
        task_description: Natural language task for the external agent.
        context: Optional context dict passed through to the remote agent.
        auth_token: Bearer token for the external agent (empty = anonymous).
        timeout: Request timeout in seconds.

    Returns:
        dict with ``"output"``, ``"status"``, and optionally ``"artifacts"``
        or ``"error"``.
    """
    # SSRF guard — external agent endpoints must be public
    try:
        assert_public_url(agent_endpoint, context="A2A outbound call")
    except SSRFError as exc:
        logger.warning("a2a_outbound_ssrf_blocked", url=agent_endpoint[:100])
        return {
            "output": str(exc),
            "status": "error",
            "error": "URL blocked by security policy",
        }

    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                agent_endpoint,
                json={
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": task_description}],
                    },
                    "context": context or {},
                },
                headers=headers,
            )

        if resp.status_code >= 400:
            logger.warning(
                "a2a_outbound_error",
                status=resp.status_code,
                endpoint=agent_endpoint[:100],
            )
            return {
                "output": f"External agent returned error {resp.status_code}",
                "status": "error",
                "http_status": resp.status_code,
            }

        data: dict[str, Any] = resp.json()
        # Extract output from A2A response format — probe common field names
        output = (
            data.get("output")
            or data.get("result")
            or data.get("message", {}).get("content", "")
            or str(data)
        )

        return {
            "output": output,
            "status": "completed",
            "raw": data,
        }

    except httpx.TimeoutException:
        return {
            "output": f"External agent timed out after {timeout}s",
            "status": "timeout",
        }
    except Exception as exc:
        logger.warning("a2a_outbound_exception", error=str(exc)[:100])
        return {"output": str(exc), "status": "error"}
