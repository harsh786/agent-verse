"""Gleam.io MCP server — contest, giveaway, and viral marketing campaigns.

Environment:
  GLEAM_API_KEY: Gleam.io API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://gleam.io/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "gleam_list_campaigns",
        "description": "List all campaigns (competitions and rewards) in the Gleam account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Campaigns per page"},
                "type": {"type": "string", "description": "Campaign type: competition, reward, gallery"},
            },
        },
    },
    {
        "name": "gleam_get_campaign_entries",
        "description": "Get entries (participants) for a specific Gleam campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign ID"},
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Entries per page"},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "gleam_get_stats",
        "description": "Get performance statistics for a Gleam campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign ID"},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "gleam_list_competitors",
        "description": "List competitor campaigns discovered in Gleam's analytics",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "gleam_create_campaign",
        "description": "Create a new competition or rewards campaign in Gleam",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Campaign name"},
                "campaign_type": {"type": "string", "description": "Type: competition, reward"},
                "starts_at": {"type": "string", "description": "Campaign start ISO datetime"},
                "ends_at": {"type": "string", "description": "Campaign end ISO datetime"},
                "prize": {"type": "string", "description": "Prize description"},
            },
            "required": ["name", "campaign_type"],
        },
    },
    {
        "name": "gleam_export_entrants",
        "description": "Export entrant data for a Gleam campaign as a list",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign ID to export"},
                "format": {"type": "string", "description": "Export format: json or csv"},
            },
            "required": ["campaign_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("GLEAM_API_KEY", "")
    if not api_key:
        return {"error": "GLEAM_API_KEY not configured"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "gleam_list_campaigns":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/campaigns", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "gleam_get_campaign_entries":
                campaign_id = arguments["campaign_id"]
                params = {k: v for k, v in arguments.items() if k != "campaign_id" and v is not None}
                r = await client.get(
                    f"{BASE_URL}/campaigns/{campaign_id}/entries",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gleam_get_stats":
                r = await client.get(
                    f"{BASE_URL}/campaigns/{arguments['campaign_id']}/stats",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gleam_list_competitors":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/competitors", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "gleam_create_campaign":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/campaigns", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "gleam_export_entrants":
                campaign_id = arguments["campaign_id"]
                r = await client.get(
                    f"{BASE_URL}/campaigns/{campaign_id}/entrants/export",
                    headers=headers,
                    params={"format": arguments.get("format", "json")},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
