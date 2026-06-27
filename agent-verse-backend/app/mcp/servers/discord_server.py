"""Discord MCP server — interact with Discord API v10.

Environment:
  DISCORD_BOT_TOKEN: Bot token (Bot ...)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

DISCORD_BASE = "https://discord.com/api/v10"

TOOL_DEFINITIONS = [
    {
        "name": "discord_send_message",
        "description": "Send a message to a Discord channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Target channel ID"},
                "content": {"type": "string", "description": "Message content"},
                "tts": {"type": "boolean", "default": False},
            },
            "required": ["channel_id", "content"],
        },
    },
    {
        "name": "discord_list_guilds",
        "description": "List all guilds (servers) the bot belongs to",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "discord_list_channels",
        "description": "List channels in a guild",
        "parameters": {
            "type": "object",
            "properties": {
                "guild_id": {"type": "string", "description": "Guild/server ID"},
            },
            "required": ["guild_id"],
        },
    },
    {
        "name": "discord_get_messages",
        "description": "Get recent messages from a channel (up to 50)",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "discord_create_thread",
        "description": "Create a new thread in a channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "name": {"type": "string", "description": "Thread name"},
                "auto_archive_duration": {
                    "type": "integer",
                    "description": "Minutes until auto-archive: 60, 1440, 4320, or 10080",
                    "default": 1440,
                },
            },
            "required": ["channel_id", "name"],
        },
    },
    {
        "name": "discord_add_reaction",
        "description": "Add a reaction to a message",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "message_id": {"type": "string"},
                "emoji": {"type": "string", "description": "Emoji string, e.g. '👍' or 'name:id'"},
            },
            "required": ["channel_id", "message_id", "emoji"],
        },
    },
    {
        "name": "discord_delete_message",
        "description": "Delete a message from a channel",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string"},
                "message_id": {"type": "string"},
            },
            "required": ["channel_id", "message_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("DISCORD_BOT_TOKEN"):
        return {"error": "DISCORD_BOT_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=DISCORD_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "discord_send_message":
                payload: dict[str, Any] = {
                    "content": arguments["content"],
                    "tts": arguments.get("tts", False),
                }
                r = await c.post(
                    f"/channels/{arguments['channel_id']}/messages", json=payload
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data["id"], "channel_id": data["channel_id"]}

            elif tool_name == "discord_list_guilds":
                r = await c.get("/users/@me/guilds")
                r.raise_for_status()
                return {
                    "guilds": [
                        {"id": g["id"], "name": g["name"]} for g in r.json()
                    ]
                }

            elif tool_name == "discord_list_channels":
                r = await c.get(f"/guilds/{arguments['guild_id']}/channels")
                r.raise_for_status()
                return {
                    "channels": [
                        {
                            "id": ch["id"],
                            "name": ch.get("name", ""),
                            "type": ch.get("type"),
                        }
                        for ch in r.json()
                    ]
                }

            elif tool_name == "discord_get_messages":
                r = await c.get(
                    f"/channels/{arguments['channel_id']}/messages",
                    params={"limit": min(arguments.get("limit", 50), 100)},
                )
                r.raise_for_status()
                return {
                    "messages": [
                        {
                            "id": m["id"],
                            "author": m.get("author", {}).get("username", ""),
                            "content": m.get("content", ""),
                            "timestamp": m.get("timestamp"),
                        }
                        for m in r.json()
                    ]
                }

            elif tool_name == "discord_create_thread":
                payload = {
                    "name": arguments["name"],
                    "auto_archive_duration": arguments.get("auto_archive_duration", 1440),
                }
                r = await c.post(
                    f"/channels/{arguments['channel_id']}/threads", json=payload
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data["id"], "name": data.get("name")}

            elif tool_name == "discord_add_reaction":
                import urllib.parse
                emoji = urllib.parse.quote(arguments["emoji"])
                r = await c.put(
                    f"/channels/{arguments['channel_id']}/messages"
                    f"/{arguments['message_id']}/reactions/{emoji}/@me"
                )
                return {"success": r.status_code == 204}

            elif tool_name == "discord_delete_message":
                r = await c.delete(
                    f"/channels/{arguments['channel_id']}/messages/{arguments['message_id']}"
                )
                return {"success": r.status_code == 204}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("discord_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
