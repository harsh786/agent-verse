"""Sportradar MCP server — live scores, schedules, team stats, and sports data.

Environment:
  SPORTRADAR_API_KEY: Sportradar API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.sportradar.com"

TOOL_DEFINITIONS = [
    {
        "name": "sportradar_get_live_scores",
        "description": "Get live scores and game status for a sport on a given day",
        "parameters": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "Sport identifier (nfl, nba, nhl, mlb, soccer)"},
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format (default: today)"},
            },
            "required": ["sport"],
        },
    },
    {
        "name": "sportradar_list_schedules",
        "description": "Get the game schedule for a sport in a specific season",
        "parameters": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "Sport identifier"},
                "season_year": {"type": "integer", "description": "Season year (e.g. 2024)"},
                "season_type": {"type": "string", "description": "Season type: REG, PST, PRE"},
            },
            "required": ["sport", "season_year"],
        },
    },
    {
        "name": "sportradar_get_team_stats",
        "description": "Get season statistics for a specific team",
        "parameters": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "Sport identifier"},
                "team_id": {"type": "string", "description": "Sportradar team ID (UUID)"},
                "season_year": {"type": "integer", "description": "Season year"},
            },
            "required": ["sport", "team_id"],
        },
    },
    {
        "name": "sportradar_get_player_profile",
        "description": "Get detailed profile and career statistics for a player",
        "parameters": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "Sport identifier"},
                "player_id": {"type": "string", "description": "Sportradar player ID (UUID)"},
            },
            "required": ["sport", "player_id"],
        },
    },
    {
        "name": "sportradar_get_standings",
        "description": "Get current league standings for a sport season",
        "parameters": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "description": "Sport identifier"},
                "season_year": {"type": "integer", "description": "Season year"},
                "season_type": {"type": "string", "description": "Season type: REG"},
            },
            "required": ["sport", "season_year"],
        },
    },
    {
        "name": "sportradar_search_teams",
        "description": "Search for teams by name across supported sports",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Team name to search for"},
                "sport": {"type": "string", "description": "Narrow search to a specific sport"},
            },
            "required": ["name"],
        },
    },
]

_SPORT_APIS: dict[str, str] = {
    "nfl": "nfl/official/v7/en",
    "nba": "nba/production/v8/en",
    "nhl": "nhl/production/v8/en",
    "mlb": "mlb/production/v8/en",
    "soccer": "soccer/production/v3/en",
}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("SPORTRADAR_API_KEY", "")
    if not api_key:
        return {"error": "SPORTRADAR_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            params: dict[str, Any] = {"api_key": api_key}

            if tool_name == "sportradar_get_live_scores":
                sport = arguments["sport"].lower()
                path = _SPORT_APIS.get(sport, f"{sport}/production/v8/en")
                date = arguments.get("date", "")
                r = await client.get(
                    f"{BASE_URL}/{path}/games/{date}/boxscore.json",
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "sportradar_list_schedules":
                sport = arguments["sport"].lower()
                path = _SPORT_APIS.get(sport, f"{sport}/production/v8/en")
                year = arguments["season_year"]
                stype = arguments.get("season_type", "REG")
                r = await client.get(
                    f"{BASE_URL}/{path}/games/{year}/{stype}/schedule.json",
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "sportradar_get_team_stats":
                sport = arguments["sport"].lower()
                path = _SPORT_APIS.get(sport, f"{sport}/production/v8/en")
                year = arguments.get("season_year", 2024)
                r = await client.get(
                    f"{BASE_URL}/{path}/teams/{arguments['team_id']}/profile.json",
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "sportradar_get_player_profile":
                sport = arguments["sport"].lower()
                path = _SPORT_APIS.get(sport, f"{sport}/production/v8/en")
                r = await client.get(
                    f"{BASE_URL}/{path}/players/{arguments['player_id']}/profile.json",
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "sportradar_get_standings":
                sport = arguments["sport"].lower()
                path = _SPORT_APIS.get(sport, f"{sport}/production/v8/en")
                year = arguments["season_year"]
                stype = arguments.get("season_type", "REG")
                r = await client.get(
                    f"{BASE_URL}/{path}/seasons/{year}/{stype}/standings.json",
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "sportradar_search_teams":
                r = await client.get(
                    f"{BASE_URL}/nfl/official/v7/en/teams/search.json",
                    params={**params, "name": arguments["name"]},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
