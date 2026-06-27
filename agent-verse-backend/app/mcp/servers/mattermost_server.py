"""Mattermost MCP server — interact with Mattermost REST API v4.

Environment:
  MATTERMOST_URL: Base URL, e.g. https://mattermost.example.com
  MATTERMOST_TOKEN: Personal access token or bot token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "mattermost_send_message",
        "description": "Post a message to a Mattermost channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "message": {"type": "string"},
                "root_id": {"type": "string", "description": "Thread root post ID for replies"},
            },
            "required": ["channel_id", "message"],
        },
    },
    {
        "name": "mattermost_list_channels",
        "description": "List public channels in a team",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "page": {"type": "integer", "default": 0},
                "per_page": {"type": "integer", "default": 60},
            },
            "required": ["team_id"],
        },
    },
    {
        "name": "mattermost_get_posts",
        "description": "Get recent posts from a channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "page": {"type": "integer", "default": 0},
                "per_page": {"type": "integer", "default": 30},
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "mattermost_list_teams",
        "description": "List all teams the authenticated user belongs to",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "mattermost_create_channel",
        "description": "Create a new channel in a team",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "name": {"type": "string", "description": "URL-safe channel name (slug)"},
                "display_name": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["O", "P"],
                    "description": "O=public, P=private",
                    "default": "O",
                },
                "purpose": {"type": "string", "default": ""},
            },
            "required": ["team_id", "name", "display_name"],
        },
    },
    {
        "name": "mattermost_search_posts",
        "description": "Search posts across Mattermost",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "terms": {"type": "string"},
                "is_or_search": {"type": "boolean", "default": False},
            },
            "required": ["team_id", "terms"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("MATTERMOST_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _base() -> str:
    return os.getenv("MATTERMOST_URL", "").rstrip("/") + "/api/v4"


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("MATTERMOST_URL") or not os.getenv("MATTERMOST_TOKEN"):
        return {"error": "MATTERMOST_URL and MATTERMOST_TOKEN required"}

    base = _base()

    try:
        async with httpx.AsyncClient(
            headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "mattermost_send_message":
                payload: dict[str, Any] = {
                    "channel_id": arguments["channel_id"],
                    "message": arguments["message"],
                }
                if "root_id" in arguments:
                    payload["root_id"] = arguments["root_id"]
                r = await c.post(f"{base}/posts", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "create_at": data.get("create_at")}

            elif tool_name == "mattermost_list_channels":
                r = await c.get(
                    f"{base}/teams/{arguments['team_id']}/channels",
                    params={
                        "page": arguments.get("page", 0),
                        "per_page": arguments.get("per_page", 60),
                    },
                )
                r.raise_for_status()
                return {
                    "channels": [
                        {
                            "id": ch["id"],
                            "name": ch.get("name"),
                            "display_name": ch.get("display_name"),
                            "type": ch.get("type"),
                        }
                        for ch in r.json()
                    ]
                }

            elif tool_name == "mattermost_get_posts":
                r = await c.get(
                    f"{base}/channels/{arguments['channel_id']}/posts",
                    params={
                        "page": arguments.get("page", 0),
                        "per_page": arguments.get("per_page", 30),
                    },
                )
                r.raise_for_status()
                data = r.json()
                order = data.get("order", [])
                posts = data.get("posts", {})
                return {
                    "posts": [
                        {
                            "id": pid,
                            "message": posts[pid].get("message", ""),
                            "user_id": posts[pid].get("user_id"),
                            "create_at": posts[pid].get("create_at"),
                        }
                        for pid in order
                        if pid in posts
                    ]
                }

            elif tool_name == "mattermost_list_teams":
                r = await c.get(f"{base}/users/me/teams")
                r.raise_for_status()
                return {
                    "teams": [
                        {"id": t["id"], "name": t.get("name"), "display_name": t.get("display_name")}
                        for t in r.json()
                    ]
                }

            elif tool_name == "mattermost_create_channel":
                payload = {
                    "team_id": arguments["team_id"],
                    "name": arguments["name"],
                    "display_name": arguments["display_name"],
                    "type": arguments.get("type", "O"),
                    "purpose": arguments.get("purpose", ""),
                }
                r = await c.post(f"{base}/channels", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "name": data.get("name")}

            elif tool_name == "mattermost_search_posts":
                payload = {
                    "terms": arguments["terms"],
                    "is_or_search": arguments.get("is_or_search", False),
                }
                r = await c.post(
                    f"{base}/teams/{arguments['team_id']}/posts/search", json=payload
                )
                r.raise_for_status()
                data = r.json()
                posts = data.get("posts", {})
                return {
                    "posts": [
                        {"id": pid, "message": posts[pid].get("message", "")[:500]}
                        for pid in data.get("order", [])
                        if pid in posts
                    ]
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("mattermost_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
