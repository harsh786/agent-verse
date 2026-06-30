"""Gust MCP server — startup funding platform, investor profiles, and applications.

Environment:
  GUST_ACCESS_TOKEN: Gust OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.gust.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "gust_list_startups",
        "description": "List startups accessible through the Gust account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "gust_get_startup",
        "description": "Get detailed information about a specific startup on Gust",
        "parameters": {
            "type": "object",
            "properties": {
                "startup_id": {"type": "string", "description": "Gust startup ID"},
            },
            "required": ["startup_id"],
        },
    },
    {
        "name": "gust_search_investors",
        "description": "Search for investors on Gust by criteria",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for investor name or focus"},
                "stage": {"type": "string", "description": "Investment stage preference"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "gust_get_funding_data",
        "description": "Get funding rounds and investment data for a startup",
        "parameters": {
            "type": "object",
            "properties": {
                "startup_id": {"type": "string", "description": "Startup ID"},
            },
            "required": ["startup_id"],
        },
    },
    {
        "name": "gust_list_applications",
        "description": "List funding applications submitted through Gust",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by application status"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "gust_get_profile",
        "description": "Get the authenticated user's Gust investor or entrepreneur profile",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("GUST_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "GUST_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "gust_list_startups":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/companies", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "gust_get_startup":
                r = await client.get(
                    f"{BASE_URL}/companies/{arguments['startup_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gust_search_investors":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/investors", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "gust_get_funding_data":
                r = await client.get(
                    f"{BASE_URL}/companies/{arguments['startup_id']}/funding_rounds",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gust_list_applications":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/applications", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "gust_get_profile":
                r = await client.get(f"{BASE_URL}/profile", headers=headers)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
