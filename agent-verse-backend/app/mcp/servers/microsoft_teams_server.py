"""Microsoft Teams MCP server — interact via MS Graph API.

Environment:
  TEAMS_ACCESS_TOKEN: OAuth2 access token for Microsoft Graph
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

TOOL_DEFINITIONS = [
    {
        "name": "teams_list_teams",
        "description": "List all Teams the authenticated user has joined",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "teams_list_channels",
        "description": "List channels in a Microsoft Team",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Team ID (GUID)"},
            },
            "required": ["team_id"],
        },
    },
    {
        "name": "teams_send_message",
        "description": "Send a message to a Teams channel",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "channel_id": {"type": "string"},
                "content": {"type": "string", "description": "Message body (HTML supported)"},
                "content_type": {
                    "type": "string",
                    "enum": ["text", "html"],
                    "default": "text",
                },
            },
            "required": ["team_id", "channel_id", "content"],
        },
    },
    {
        "name": "teams_create_channel",
        "description": "Create a new channel in a Microsoft Team",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "display_name": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "membership_type": {
                    "type": "string",
                    "enum": ["standard", "private"],
                    "default": "standard",
                },
            },
            "required": ["team_id", "display_name"],
        },
    },
    {
        "name": "teams_list_messages",
        "description": "Get messages from a Teams channel",
        "parameters": {
            "type": "object",
            "properties": {
                "team_id": {"type": "string"},
                "channel_id": {"type": "string"},
                "top": {"type": "integer", "default": 20},
            },
            "required": ["team_id", "channel_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("TEAMS_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("TEAMS_ACCESS_TOKEN"):
        return {"error": "TEAMS_ACCESS_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=GRAPH_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "teams_list_teams":
                r = await c.get("/me/joinedTeams")
                r.raise_for_status()
                data = r.json()
                return {
                    "teams": [
                        {"id": t["id"], "displayName": t.get("displayName", "")}
                        for t in data.get("value", [])
                    ]
                }

            elif tool_name == "teams_list_channels":
                r = await c.get(f"/teams/{arguments['team_id']}/channels")
                r.raise_for_status()
                data = r.json()
                return {
                    "channels": [
                        {
                            "id": ch["id"],
                            "displayName": ch.get("displayName", ""),
                            "description": ch.get("description", ""),
                        }
                        for ch in data.get("value", [])
                    ]
                }

            elif tool_name == "teams_send_message":
                payload: dict[str, Any] = {
                    "body": {
                        "contentType": arguments.get("content_type", "text"),
                        "content": arguments["content"],
                    }
                }
                r = await c.post(
                    f"/teams/{arguments['team_id']}/channels/{arguments['channel_id']}/messages",
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "created_at": data.get("createdDateTime")}

            elif tool_name == "teams_create_channel":
                payload = {
                    "displayName": arguments["display_name"],
                    "description": arguments.get("description", ""),
                    "membershipType": arguments.get("membership_type", "standard"),
                }
                r = await c.post(
                    f"/teams/{arguments['team_id']}/channels", json=payload
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "displayName": data.get("displayName")}

            elif tool_name == "teams_list_messages":
                r = await c.get(
                    f"/teams/{arguments['team_id']}/channels/{arguments['channel_id']}/messages",
                    params={"$top": arguments.get("top", 20)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "messages": [
                        {
                            "id": m["id"],
                            "from": m.get("from", {}).get("user", {}).get("displayName", ""),
                            "body": m.get("body", {}).get("content", "")[:500],
                            "created_at": m.get("createdDateTime"),
                        }
                        for m in data.get("value", [])
                    ]
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("teams_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
