"""Steam MCP server — gaming platform data, player profiles, and game info.

Environment:
  STEAM_API_KEY: Steam Web API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.steampowered.com"

TOOL_DEFINITIONS = [
    {
        "name": "steam_get_player_summary",
        "description": "Get public profile information for one or more Steam players",
        "parameters": {
            "type": "object",
            "properties": {
                "steam_ids": {
                    "type": "array",
                    "description": "List of 64-bit Steam IDs to look up",
                    "items": {"type": "string"},
                },
            },
            "required": ["steam_ids"],
        },
    },
    {
        "name": "steam_get_owned_games",
        "description": "Get the list of games owned by a Steam user",
        "parameters": {
            "type": "object",
            "properties": {
                "steam_id": {"type": "string", "description": "64-bit Steam ID of the user"},
                "include_appinfo": {"type": "boolean", "description": "Include game name and logo URL"},
                "include_played_free_games": {"type": "boolean", "description": "Include free-to-play games"},
            },
            "required": ["steam_id"],
        },
    },
    {
        "name": "steam_get_recently_played",
        "description": "Get games a Steam user has played recently",
        "parameters": {
            "type": "object",
            "properties": {
                "steam_id": {"type": "string", "description": "64-bit Steam ID"},
                "count": {"type": "integer", "description": "Number of recent games to return"},
            },
            "required": ["steam_id"],
        },
    },
    {
        "name": "steam_get_game_news",
        "description": "Get the latest news articles for a specific Steam game",
        "parameters": {
            "type": "object",
            "properties": {
                "app_id": {"type": "integer", "description": "Steam Application ID of the game"},
                "count": {"type": "integer", "description": "Number of news items to return"},
                "max_length": {"type": "integer", "description": "Max characters per news item body"},
            },
            "required": ["app_id"],
        },
    },
    {
        "name": "steam_get_achievement_stats",
        "description": "Get achievement statistics for a player in a specific game",
        "parameters": {
            "type": "object",
            "properties": {
                "steam_id": {"type": "string", "description": "64-bit Steam ID"},
                "app_id": {"type": "integer", "description": "Steam Application ID"},
            },
            "required": ["steam_id", "app_id"],
        },
    },
    {
        "name": "steam_search_games",
        "description": "Search for games in the Steam store by name",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for game name"},
                "cc": {"type": "string", "description": "Country code for pricing (e.g. US)"},
                "language": {"type": "string", "description": "Language code (e.g. en)"},
            },
            "required": ["query"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("STEAM_API_KEY", "")
    if not api_key:
        return {"error": "STEAM_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "steam_get_player_summary":
                r = await client.get(
                    f"{BASE_URL}/ISteamUser/GetPlayerSummaries/v0002/",
                    params={
                        "key": api_key,
                        "steamids": ",".join(arguments["steam_ids"]),
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "steam_get_owned_games":
                params: dict[str, Any] = {
                    "key": api_key,
                    "steamid": arguments["steam_id"],
                    "format": "json",
                    "include_appinfo": int(arguments.get("include_appinfo", True)),
                    "include_played_free_games": int(arguments.get("include_played_free_games", False)),
                }
                r = await client.get(f"{BASE_URL}/IPlayerService/GetOwnedGames/v0001/", params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "steam_get_recently_played":
                params = {
                    "key": api_key,
                    "steamid": arguments["steam_id"],
                    "format": "json",
                }
                if "count" in arguments:
                    params["count"] = arguments["count"]
                r = await client.get(f"{BASE_URL}/IPlayerService/GetRecentlyPlayedGames/v0001/", params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "steam_get_game_news":
                params = {
                    "appid": arguments["app_id"],
                    "count": arguments.get("count", 5),
                    "maxlength": arguments.get("max_length", 300),
                    "format": "json",
                }
                r = await client.get(f"{BASE_URL}/ISteamNews/GetNewsForApp/v0002/", params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "steam_get_achievement_stats":
                r = await client.get(
                    f"{BASE_URL}/ISteamUserStats/GetPlayerAchievements/v0001/",
                    params={
                        "key": api_key,
                        "steamid": arguments["steam_id"],
                        "appid": arguments["app_id"],
                        "format": "json",
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "steam_search_games":
                params = {
                    "term": arguments["query"],
                    "cc": arguments.get("cc", "US"),
                    "l": arguments.get("language", "en"),
                    "json": 1,
                }
                r = await client.get("https://store.steampowered.com/api/storesearch/", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
