"""Slack MCP server — interact with Slack API.

Environment:
  SLACK_BOT_TOKEN: Bot token (xoxb-...)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "slack_send_message",
        "description": "Send a message to a Slack channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "text": {"type": "string"},
                "thread_ts": {
                    "type": "string",
                    "description": "Reply to thread",
                },
            },
            "required": ["channel", "text"],
        },
    },
    {
        "name": "slack_list_channels",
        "description": "List public Slack channels",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "slack_get_channel_history",
        "description": "Get recent messages from a channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["channel"],
        },
    },
    {
        "name": "slack_search_messages",
        "description": "Search Slack messages",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "count": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
]

SLACK_API = "https://slack.com/api"


def _token() -> str:
    return os.getenv("SLACK_BOT_TOKEN", "")


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return await _call_tool_inner(tool_name, arguments, credentials=credentials)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.text[:500]
        except Exception:
            pass
        return {
            "error": f"HTTP {exc.response.status_code}: {error_body or exc.response.reason_phrase}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}


async def _call_tool_inner(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    creds = credentials or {}
    token = (
        creds.get("token")
        or creds.get("bot_token")
        or creds.get("api_token")
        or _token()
    )
    if not token:
        return {"error": "SLACK_BOT_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        if tool_name == "slack_send_message":
            payload: dict[str, Any] = {
                "channel": arguments["channel"],
                "text": arguments["text"],
            }
            if "thread_ts" in arguments:
                payload["thread_ts"] = arguments["thread_ts"]
            resp = await client.post(
                f"{SLACK_API}/chat.postMessage", json=payload, headers=headers
            )
            data = resp.json()
            return {
                "ok": data.get("ok"),
                "ts": data.get("ts"),
                "channel": data.get("channel"),
            }

        elif tool_name == "slack_list_channels":
            resp = await client.get(
                f"{SLACK_API}/conversations.list",
                params={"limit": arguments.get("limit", 20)},
                headers=headers,
            )
            data = resp.json()
            if not data.get("ok"):
                return {"error": data.get("error", "Slack API error"), "detail": data.get("response_metadata", {})}
            return {
                "channels": [
                    {"id": c["id"], "name": c["name"]}
                    for c in data.get("channels", [])
                ]
            }

        elif tool_name == "slack_get_channel_history":
            resp = await client.get(
                f"{SLACK_API}/conversations.history",
                params={
                    "channel": arguments["channel"],
                    "limit": arguments.get("limit", 20),
                },
                headers=headers,
            )
            data = resp.json()
            if not data.get("ok"):
                return {"error": data.get("error", "Slack API error"), "detail": data.get("response_metadata", {})}
            return {
                "messages": [
                    {
                        "ts": m["ts"],
                        "text": m.get("text", ""),
                        "user": m.get("user", ""),
                    }
                    for m in data.get("messages", [])
                ]
            }

        elif tool_name == "slack_search_messages":
            resp = await client.get(
                f"{SLACK_API}/search.messages",
                params={
                    "query": arguments["query"],
                    "count": arguments.get("count", 10),
                },
                headers=headers,
            )
            data = resp.json()
            if not data.get("ok"):
                return {"error": data.get("error", "Slack API error"), "detail": data.get("response_metadata", {})}
            matches = data.get("messages", {}).get("matches", [])
            return {
                "messages": [
                    {
                        "text": m.get("text", ""),
                        "channel": m.get("channel", {}).get("name", ""),
                        "ts": m.get("ts", ""),
                    }
                    for m in matches
                ]
            }

        return {"error": f"Unknown tool: {tool_name}"}
