"""Zenloop MCP server — NPS surveys, feedback collection, and customer sentiment.

Environment:
  ZENLOOP_API_TOKEN: Zenloop API token for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.zenloop.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "zenloop_list_surveys",
        "description": "List all NPS surveys in the Zenloop account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "zenloop_get_responses",
        "description": "Get survey responses for a specific survey with optional date filters",
        "parameters": {
            "type": "object",
            "properties": {
                "survey_id": {"type": "string", "description": "ID of the survey"},
                "date_from": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "date_to": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Responses per page"},
            },
            "required": ["survey_id"],
        },
    },
    {
        "name": "zenloop_create_survey",
        "description": "Create a new NPS survey in Zenloop",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Internal name for the survey"},
                "language": {"type": "string", "description": "Survey language code (e.g. en, de)"},
                "question": {"type": "string", "description": "Custom NPS question text"},
                "followup_question_positive": {"type": "string", "description": "Follow-up for promoters"},
                "followup_question_negative": {"type": "string", "description": "Follow-up for detractors"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "zenloop_get_nps_score",
        "description": "Get the current NPS score and breakdown for a survey",
        "parameters": {
            "type": "object",
            "properties": {
                "survey_id": {"type": "string", "description": "ID of the survey"},
                "date_from": {"type": "string", "description": "Start date for the NPS calculation"},
                "date_to": {"type": "string", "description": "End date for the NPS calculation"},
            },
            "required": ["survey_id"],
        },
    },
    {
        "name": "zenloop_list_segments",
        "description": "List customer segments defined in the Zenloop account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Segments per page"},
            },
        },
    },
    {
        "name": "zenloop_trigger_survey",
        "description": "Trigger a survey to be sent to a specific recipient",
        "parameters": {
            "type": "object",
            "properties": {
                "survey_id": {"type": "string", "description": "ID of the survey to send"},
                "recipient_email": {"type": "string", "description": "Email address of the recipient"},
                "recipient_name": {"type": "string", "description": "Full name of the recipient"},
                "custom_attributes": {"type": "object", "description": "Extra attributes for personalization"},
            },
            "required": ["survey_id", "recipient_email"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_token = os.getenv("ZENLOOP_API_TOKEN", "")
    if not api_token:
        return {"error": "ZENLOOP_API_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "zenloop_list_surveys":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/surveys", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "zenloop_get_responses":
                survey_id = arguments["survey_id"]
                params = {k: v for k, v in arguments.items() if k != "survey_id" and v is not None}
                r = await client.get(
                    f"{BASE_URL}/surveys/{survey_id}/answers",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "zenloop_create_survey":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/surveys", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "zenloop_get_nps_score":
                survey_id = arguments["survey_id"]
                params = {k: v for k, v in arguments.items() if k != "survey_id" and v is not None}
                r = await client.get(
                    f"{BASE_URL}/surveys/{survey_id}/score",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "zenloop_list_segments":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/segments", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "zenloop_trigger_survey":
                survey_id = arguments["survey_id"]
                payload = {
                    "recipient": {
                        "email": arguments["recipient_email"],
                        "name": arguments.get("recipient_name", ""),
                    }
                }
                if "custom_attributes" in arguments:
                    payload["recipient"]["custom_attributes"] = arguments["custom_attributes"]
                r = await client.post(
                    f"{BASE_URL}/surveys/{survey_id}/dispatch",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
