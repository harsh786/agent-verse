"""Salesloft MCP server — sales engagement: people, cadences, calls, and analytics.

Environment variables:
  SALESLOFT_API_KEY: Salesloft API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SALESLOFT_BASE = "https://api.salesloft.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "salesloft_list_people",
        "description": "List people (prospects) in Salesloft with optional filtering and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 25},
                "page": {"type": "integer", "default": 1},
                "email_address": {"type": "string", "description": "Filter by email address"},
                "sort_by": {"type": "string", "description": "Field to sort by"},
                "sort_direction": {"type": "string", "enum": ["ASC", "DESC"], "default": "DESC"},
            },
        },
    },
    {
        "name": "salesloft_create_person",
        "description": "Create a new person (prospect) record in Salesloft",
        "parameters": {
            "type": "object",
            "properties": {
                "email_address": {"type": "string", "description": "Person's email address"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "title": {"type": "string"},
                "company": {"type": "string"},
                "phone": {"type": "string"},
                "owner_id": {"type": "integer", "description": "Salesloft user ID to assign ownership"},
            },
            "required": ["email_address"],
        },
    },
    {
        "name": "salesloft_list_cadences",
        "description": "List cadences (sequences) in Salesloft",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 25},
                "page": {"type": "integer", "default": 1},
                "search": {"type": "string", "description": "Search by cadence name"},
            },
        },
    },
    {
        "name": "salesloft_add_to_cadence",
        "description": "Add a person to a cadence (email/call sequence) in Salesloft",
        "parameters": {
            "type": "object",
            "properties": {
                "person_id": {"type": "integer", "description": "Salesloft person ID"},
                "cadence_id": {"type": "integer", "description": "Cadence ID to enrol the person in"},
                "user_id": {"type": "integer", "description": "Sending user ID"},
                "step_id": {"type": "integer", "description": "Specific step to start at (optional)"},
            },
            "required": ["person_id", "cadence_id"],
        },
    },
    {
        "name": "salesloft_list_calls",
        "description": "List call records in Salesloft with optional date filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 25},
                "page": {"type": "integer", "default": 1},
                "updated_at[gt]": {"type": "string", "description": "Filter calls updated after date (ISO 8601)"},
                "person_id": {"type": "integer", "description": "Filter by person ID"},
            },
        },
    },
    {
        "name": "salesloft_get_analytics",
        "description": "Get aggregate analytics and engagement metrics for the Salesloft account",
        "parameters": {
            "type": "object",
            "properties": {
                "user_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Filter by specific user IDs",
                },
                "date_range_start": {"type": "string", "description": "Report start date (YYYY-MM-DD)"},
                "date_range_end": {"type": "string", "description": "Report end date (YYYY-MM-DD)"},
            },
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("SALESLOFT_API_KEY", "")
    if not api_key:
        return {"error": "SALESLOFT_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=SALESLOFT_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "salesloft_list_people":
                params: dict[str, Any] = {
                    "per_page": arguments.get("per_page", 25),
                    "page": arguments.get("page", 1),
                    "sort_direction": arguments.get("sort_direction", "DESC"),
                }
                if "email_address" in arguments:
                    params["email_address"] = arguments["email_address"]
                if "sort_by" in arguments:
                    params["sort_by"] = arguments["sort_by"]
                r = await c.get("/people.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "salesloft_create_person":
                body: dict[str, Any] = {"email_address": arguments["email_address"]}
                for k in ("first_name", "last_name", "title", "company", "phone", "owner_id"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/people.json", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "salesloft_list_cadences":
                params = {
                    "per_page": arguments.get("per_page", 25),
                    "page": arguments.get("page", 1),
                }
                if "search" in arguments:
                    params["search"] = arguments["search"]
                r = await c.get("/cadences.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "salesloft_add_to_cadence":
                body = {
                    "person_id": arguments["person_id"],
                    "cadence_id": arguments["cadence_id"],
                }
                if "user_id" in arguments:
                    body["user_id"] = arguments["user_id"]
                if "step_id" in arguments:
                    body["step_id"] = arguments["step_id"]
                r = await c.post("/cadence_memberships.json", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "salesloft_list_calls":
                params = {
                    "per_page": arguments.get("per_page", 25),
                    "page": arguments.get("page", 1),
                }
                if "updated_at[gt]" in arguments:
                    params["updated_at[gt]"] = arguments["updated_at[gt]"]
                if "person_id" in arguments:
                    params["person_id"] = arguments["person_id"]
                r = await c.get("/activities/calls.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "salesloft_get_analytics":
                params = {}
                if "user_ids" in arguments:
                    params["user_ids[]"] = arguments["user_ids"]
                if "date_range_start" in arguments:
                    params["date_range_start"] = arguments["date_range_start"]
                if "date_range_end" in arguments:
                    params["date_range_end"] = arguments["date_range_end"]
                r = await c.get("/analytics/summary.json", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("salesloft_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
