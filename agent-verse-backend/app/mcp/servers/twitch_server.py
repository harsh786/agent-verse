"""Twitch MCP server — streaming platform: streams, users, followers, videos, and channels.

Environment:
  TWITCH_CLIENT_ID: Twitch application Client ID from dev.twitch.tv
  TWITCH_ACCESS_TOKEN: Twitch OAuth2 access token (app or user token)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.twitch.tv/helix"


def _headers() -> dict[str, str]:
    return {
        "Client-ID": os.getenv("TWITCH_CLIENT_ID", ""),
        "Authorization": f"Bearer {os.getenv('TWITCH_ACCESS_TOKEN', '')}",
    }


TOOL_DEFINITIONS = [
    {
        "name": "twitch_get_streams",
        "description": "Get a list of live streams on Twitch with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "user_login": {"type": "string", "description": "Comma-separated list of streamer login names to filter"},
                "game_id": {"type": "string", "description": "Game/category ID to filter streams by"},
                "language": {"type": "string", "description": "Stream language filter (ISO 639-1 code)"},
                "first": {"type": "integer", "description": "Max results to return (1-100)", "default": 20},
            },
        },
    },
    {
        "name": "twitch_get_user",
        "description": "Retrieve Twitch user information by login name or user ID",
        "parameters": {
            "type": "object",
            "properties": {
                "login": {"type": "string", "description": "Twitch username/login to look up"},
                "id": {"type": "string", "description": "Twitch user ID to look up"},
            },
        },
    },
    {
        "name": "twitch_get_followers",
        "description": "Get a list of users who follow a specified Twitch channel",
        "parameters": {
            "type": "object",
            "properties": {
                "broadcaster_id": {"type": "string", "description": "Twitch broadcaster/channel user ID"},
                "first": {"type": "integer", "description": "Max followers to return (1-100)", "default": 20},
                "after": {"type": "string", "description": "Pagination cursor from previous response"},
            },
            "required": ["broadcaster_id"],
        },
    },
    {
        "name": "twitch_get_videos",
        "description": "Get a list of videos (VODs) for a Twitch user or game",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Twitch user ID to get videos for"},
                "game_id": {"type": "string", "description": "Game ID to get videos for"},
                "type": {"type": "string", "description": "Video type filter: all, upload, archive, highlight", "default": "all"},
                "first": {"type": "integer", "description": "Max videos to return (1-100)", "default": 20},
            },
        },
    },
    {
        "name": "twitch_search_channels",
        "description": "Search for Twitch channels by name or query string",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "live_only": {"type": "boolean", "description": "If true, only return currently live channels", "default": False},
                "first": {"type": "integer", "description": "Max results to return (1-100)", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "twitch_get_channel_info",
        "description": "Get detailed information about a Twitch channel by broadcaster ID",
        "parameters": {
            "type": "object",
            "properties": {
                "broadcaster_id": {"type": "string", "description": "Twitch broadcaster user ID"},
            },
            "required": ["broadcaster_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    client_id = os.getenv("TWITCH_CLIENT_ID", "")
    access_token = os.getenv("TWITCH_ACCESS_TOKEN", "")
    if not client_id:
        return {"error": "TWITCH_CLIENT_ID not configured"}
    if not access_token:
        return {"error": "TWITCH_ACCESS_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "twitch_get_streams":
                params: dict[str, Any] = {"first": arguments.get("first", 20)}
                if "user_login" in arguments:
                    params["user_login"] = arguments["user_login"]
                if "game_id" in arguments:
                    params["game_id"] = arguments["game_id"]
                if "language" in arguments:
                    params["language"] = arguments["language"]
                r = await client.get(f"{BASE_URL}/streams", headers=_headers(), params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "streams": [
                        {
                            "id": s.get("id"),
                            "user_name": s.get("user_name"),
                            "game_name": s.get("game_name"),
                            "title": s.get("title"),
                            "viewer_count": s.get("viewer_count"),
                        }
                        for s in data.get("data", [])
                    ],
                    "pagination": data.get("pagination", {}),
                }

            elif tool_name == "twitch_get_user":
                params = {}
                if "login" in arguments:
                    params["login"] = arguments["login"]
                if "id" in arguments:
                    params["id"] = arguments["id"]
                r = await client.get(f"{BASE_URL}/users", headers=_headers(), params=params)
                r.raise_for_status()
                data = r.json()
                users = data.get("data", [])
                return {"user": users[0] if users else None}

            elif tool_name == "twitch_get_followers":
                params = {
                    "broadcaster_id": arguments["broadcaster_id"],
                    "first": arguments.get("first", 20),
                }
                if "after" in arguments:
                    params["after"] = arguments["after"]
                r = await client.get(f"{BASE_URL}/channels/followers", headers=_headers(), params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "followers": [
                        {"user_id": f.get("user_id"), "user_name": f.get("user_name"), "followed_at": f.get("followed_at")}
                        for f in data.get("data", [])
                    ],
                    "total": data.get("total", 0),
                    "pagination": data.get("pagination", {}),
                }

            elif tool_name == "twitch_get_videos":
                params = {
                    "type": arguments.get("type", "all"),
                    "first": arguments.get("first", 20),
                }
                if "user_id" in arguments:
                    params["user_id"] = arguments["user_id"]
                if "game_id" in arguments:
                    params["game_id"] = arguments["game_id"]
                r = await client.get(f"{BASE_URL}/videos", headers=_headers(), params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "videos": [
                        {"id": v.get("id"), "title": v.get("title"), "url": v.get("url"), "view_count": v.get("view_count")}
                        for v in data.get("data", [])
                    ]
                }

            elif tool_name == "twitch_search_channels":
                params = {
                    "query": arguments["query"],
                    "first": arguments.get("first", 20),
                    "live_only": str(arguments.get("live_only", False)).lower(),
                }
                r = await client.get(f"{BASE_URL}/search/channels", headers=_headers(), params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "channels": [
                        {"id": c.get("id"), "display_name": c.get("display_name"), "game_name": c.get("game_name"), "is_live": c.get("is_live")}
                        for c in data.get("data", [])
                    ]
                }

            elif tool_name == "twitch_get_channel_info":
                r = await client.get(
                    f"{BASE_URL}/channels",
                    headers=_headers(),
                    params={"broadcaster_id": arguments["broadcaster_id"]},
                )
                r.raise_for_status()
                data = r.json()
                channels = data.get("data", [])
                return {"channel": channels[0] if channels else None}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("twitch_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
