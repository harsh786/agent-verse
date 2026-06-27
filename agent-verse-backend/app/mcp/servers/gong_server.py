"""Gong MCP server — call recordings, transcripts, users, and call statistics.

Environment variables:
  GONG_ACCESS_KEY: Gong API access key
  GONG_ACCESS_KEY_SECRET: Gong API access key secret
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GONG_BASE = "https://api.gong.io/v2"

TOOL_DEFINITIONS = [
    {
        "name": "gong_list_calls",
        "description": "List Gong calls with optional date range filters",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date_time": {
                    "type": "string",
                    "description": "ISO 8601 start datetime, e.g. 2024-01-01T00:00:00Z",
                },
                "to_date_time": {
                    "type": "string",
                    "description": "ISO 8601 end datetime",
                },
                "cursor": {"type": "string", "description": "Pagination cursor"},
                "workspace_id": {"type": "string"},
            },
        },
    },
    {
        "name": "gong_get_call_transcript",
        "description": "Get the transcript for a specific Gong call",
        "parameters": {
            "type": "object",
            "properties": {
                "call_id": {"type": "string"},
            },
            "required": ["call_id"],
        },
    },
    {
        "name": "gong_list_users",
        "description": "List all Gong users in the workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string"},
                "include_avatars": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "gong_get_call_stats",
        "description": "Get call activity statistics aggregated by date range",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date_time": {
                    "type": "string",
                    "description": "ISO 8601 start datetime",
                },
                "to_date_time": {
                    "type": "string",
                    "description": "ISO 8601 end datetime",
                },
                "workspace_id": {"type": "string"},
            },
        },
    },
]


def _auth() -> tuple[str, str]:
    return (
        os.getenv("GONG_ACCESS_KEY", ""),
        os.getenv("GONG_ACCESS_KEY_SECRET", ""),
    )


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("GONG_ACCESS_KEY") or not os.getenv("GONG_ACCESS_KEY_SECRET"):
        return {"error": "GONG_ACCESS_KEY and GONG_ACCESS_KEY_SECRET required"}

    try:
        async with httpx.AsyncClient(
            base_url=GONG_BASE, auth=_auth(), timeout=30.0
        ) as c:
            if tool_name == "gong_list_calls":
                params: dict[str, Any] = {}
                for k in ("fromDateTime", "toDateTime", "cursor", "workspaceId"):
                    # Accept both camelCase and snake_case from the caller
                    snake = {
                        "fromDateTime": "from_date_time",
                        "toDateTime": "to_date_time",
                        "cursor": "cursor",
                        "workspaceId": "workspace_id",
                    }[k]
                    if v := arguments.get(snake) or arguments.get(k):
                        params[k] = v
                r = await c.get("/calls", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gong_get_call_transcript":
                call_id = arguments["call_id"]
                r = await c.post(
                    "/calls/transcript",
                    json={"filter": {"callIds": [call_id]}},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "gong_list_users":
                params = {}
                if cursor := arguments.get("cursor"):
                    params["cursor"] = cursor
                if arguments.get("include_avatars"):
                    params["includeAvatars"] = "true"
                r = await c.get("/users", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gong_get_call_stats":
                params = {}
                if fdt := arguments.get("from_date_time"):
                    params["fromDateTime"] = fdt
                if tdt := arguments.get("to_date_time"):
                    params["toDateTime"] = tdt
                if wid := arguments.get("workspace_id"):
                    params["workspaceId"] = wid
                r = await c.get("/stats/activity/calls", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("gong_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
