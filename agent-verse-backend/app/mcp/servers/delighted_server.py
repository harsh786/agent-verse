"""Delighted MCP server — customer satisfaction surveys and NPS tracking.

Environment:
  DELIGHTED_API_KEY: Delighted API key for authentication (HTTP Basic)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.delighted.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "delighted_list_people",
        "description": "List people (survey recipients) in the Delighted account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number for pagination"},
                "per_page": {"type": "integer", "description": "Results per page"},
                "email": {"type": "string", "description": "Filter by email address"},
            },
        },
    },
    {
        "name": "delighted_create_person",
        "description": "Add a person to Delighted and optionally send them a survey immediately",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email address of the person"},
                "name": {"type": "string", "description": "Full name of the person"},
                "delay": {"type": "integer", "description": "Delay in seconds before sending survey (0 = send now)"},
                "properties": {"type": "object", "description": "Custom properties to store with the person"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "delighted_list_survey_responses",
        "description": "List survey responses with optional filters by score range and date",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Responses per page"},
                "since": {"type": "integer", "description": "Filter responses after this Unix timestamp"},
                "until": {"type": "integer", "description": "Filter responses before this Unix timestamp"},
            },
        },
    },
    {
        "name": "delighted_get_metrics",
        "description": "Get overall NPS metrics including score, promoters, passives, and detractors",
        "parameters": {
            "type": "object",
            "properties": {
                "since": {"type": "integer", "description": "Start Unix timestamp"},
                "until": {"type": "integer", "description": "End Unix timestamp"},
                "trend": {"type": "integer", "description": "Number of trend periods to include"},
            },
        },
    },
    {
        "name": "delighted_list_trends",
        "description": "List NPS trend data over time for charting",
        "parameters": {
            "type": "object",
            "properties": {
                "resolution": {"type": "string", "description": "Time resolution: day, week, month"},
                "since": {"type": "integer", "description": "Start Unix timestamp"},
                "until": {"type": "integer", "description": "End Unix timestamp"},
            },
        },
    },
    {
        "name": "delighted_add_to_unsubscribe",
        "description": "Add an email address to the unsubscribe list to stop future surveys",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email to unsubscribe from surveys"},
                "person_id": {"type": "string", "description": "Optional Delighted person ID"},
            },
            "required": ["email"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("DELIGHTED_API_KEY", "")
    if not api_key:
        return {"error": "DELIGHTED_API_KEY not configured"}

    auth = (api_key, "")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "delighted_list_people":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/people.json", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "delighted_create_person":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/people.json", auth=auth, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "delighted_list_survey_responses":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/survey_responses.json", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "delighted_get_metrics":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/metrics.json", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "delighted_list_trends":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/survey_responses/trends.json", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "delighted_add_to_unsubscribe":
                r = await client.post(
                    f"{BASE_URL}/unsubscribes.json",
                    auth=auth,
                    json={"person_email": arguments["email"]},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
