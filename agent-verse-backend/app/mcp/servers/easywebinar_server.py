"""EasyWebinar MCP server — automated webinar hosting and attendee management.

Environment:
  EASYWEBINAR_API_KEY: EasyWebinar API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://easywebinar.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "easywebinar_list_webinars",
        "description": "List all webinars in the EasyWebinar account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "type": {"type": "string", "description": "Filter by type: live, automated, hybrid"},
            },
        },
    },
    {
        "name": "easywebinar_create_webinar",
        "description": "Create a new webinar in EasyWebinar",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Webinar title"},
                "description": {"type": "string", "description": "Webinar description"},
                "webinar_type": {"type": "string", "description": "Type: live, automated"},
                "schedule_date": {"type": "string", "description": "Scheduled date/time in ISO format"},
                "timezone": {"type": "string", "description": "Timezone (e.g. America/New_York)"},
            },
            "required": ["title", "webinar_type"],
        },
    },
    {
        "name": "easywebinar_list_registrants",
        "description": "List attendees who registered for a webinar",
        "parameters": {
            "type": "object",
            "properties": {
                "webinar_id": {"type": "string", "description": "Webinar ID"},
                "page": {"type": "integer", "description": "Page number"},
            },
            "required": ["webinar_id"],
        },
    },
    {
        "name": "easywebinar_register_attendee",
        "description": "Register a new attendee for an EasyWebinar webinar",
        "parameters": {
            "type": "object",
            "properties": {
                "webinar_id": {"type": "string", "description": "Webinar ID"},
                "email": {"type": "string", "description": "Attendee email"},
                "first_name": {"type": "string", "description": "First name"},
                "last_name": {"type": "string", "description": "Last name"},
            },
            "required": ["webinar_id", "email"],
        },
    },
    {
        "name": "easywebinar_get_stats",
        "description": "Get attendance and engagement statistics for a webinar",
        "parameters": {
            "type": "object",
            "properties": {
                "webinar_id": {"type": "string", "description": "Webinar ID"},
            },
            "required": ["webinar_id"],
        },
    },
    {
        "name": "easywebinar_cancel_registration",
        "description": "Cancel an attendee's registration for a webinar",
        "parameters": {
            "type": "object",
            "properties": {
                "webinar_id": {"type": "string", "description": "Webinar ID"},
                "registrant_id": {"type": "string", "description": "Registrant ID to cancel"},
            },
            "required": ["webinar_id", "registrant_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("EASYWEBINAR_API_KEY", "")
    if not api_key:
        return {"error": "EASYWEBINAR_API_KEY not configured"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "easywebinar_list_webinars":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/webinars", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "easywebinar_create_webinar":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/webinars", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "easywebinar_list_registrants":
                webinar_id = arguments["webinar_id"]
                params = {}
                if "page" in arguments:
                    params["page"] = arguments["page"]
                r = await client.get(
                    f"{BASE_URL}/webinars/{webinar_id}/registrants",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "easywebinar_register_attendee":
                webinar_id = arguments["webinar_id"]
                r = await client.post(
                    f"{BASE_URL}/webinars/{webinar_id}/registrants",
                    headers=headers,
                    json={k: v for k, v in arguments.items() if k != "webinar_id" and v is not None},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "easywebinar_get_stats":
                r = await client.get(
                    f"{BASE_URL}/webinars/{arguments['webinar_id']}/stats",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "easywebinar_cancel_registration":
                r = await client.delete(
                    f"{BASE_URL}/webinars/{arguments['webinar_id']}/registrants/{arguments['registrant_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return {"cancelled": True}

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
