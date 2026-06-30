"""GoToWebinar MCP server — webinar creation, registration, and analytics.

Environment:
  GOTOWEBINAR_ACCESS_TOKEN: OAuth2 access token for GoToWebinar API
  GOTOWEBINAR_ORGANIZER_KEY: Organizer key for the GoToWebinar account
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.getgo.com/G2W/rest/v2"

TOOL_DEFINITIONS = [
    {
        "name": "gotowebinar_list_webinars",
        "description": "List all upcoming and past webinars for the organizer",
        "parameters": {
            "type": "object",
            "properties": {
                "from_time": {"type": "string", "description": "Start time filter in ISO 8601 format"},
                "to_time": {"type": "string", "description": "End time filter in ISO 8601 format"},
                "page": {"type": "integer", "description": "Page number for pagination"},
                "size": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "gotowebinar_create_webinar",
        "description": "Create a new GoToWebinar event",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Title of the webinar"},
                "description": {"type": "string", "description": "Description of the webinar content"},
                "times": {
                    "type": "array",
                    "description": "Array of scheduled time blocks with startTime and endTime",
                    "items": {"type": "object"},
                },
                "timezone": {"type": "string", "description": "Timezone for the webinar (e.g. America/New_York)"},
            },
            "required": ["subject", "times"],
        },
    },
    {
        "name": "gotowebinar_register_attendee",
        "description": "Register a new attendee for a GoToWebinar event",
        "parameters": {
            "type": "object",
            "properties": {
                "webinar_key": {"type": "string", "description": "Unique webinar key"},
                "first_name": {"type": "string", "description": "Attendee first name"},
                "last_name": {"type": "string", "description": "Attendee last name"},
                "email": {"type": "string", "description": "Attendee email address"},
            },
            "required": ["webinar_key", "first_name", "last_name", "email"],
        },
    },
    {
        "name": "gotowebinar_list_registrants",
        "description": "List all registrants for a specific webinar",
        "parameters": {
            "type": "object",
            "properties": {
                "webinar_key": {"type": "string", "description": "Unique webinar key"},
                "page": {"type": "integer", "description": "Page number"},
                "size": {"type": "integer", "description": "Results per page"},
            },
            "required": ["webinar_key"],
        },
    },
    {
        "name": "gotowebinar_get_performance",
        "description": "Get attendance and performance statistics for a webinar",
        "parameters": {
            "type": "object",
            "properties": {
                "webinar_key": {"type": "string", "description": "Unique webinar key"},
            },
            "required": ["webinar_key"],
        },
    },
    {
        "name": "gotowebinar_cancel_webinar",
        "description": "Cancel a scheduled GoToWebinar event and notify registrants",
        "parameters": {
            "type": "object",
            "properties": {
                "webinar_key": {"type": "string", "description": "Unique webinar key to cancel"},
                "send_cancellation_emails": {"type": "boolean", "description": "Send cancellation emails to registrants"},
            },
            "required": ["webinar_key"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("GOTOWEBINAR_ACCESS_TOKEN", "")
    organizer_key = os.getenv("GOTOWEBINAR_ORGANIZER_KEY", "")
    if not access_token:
        return {"error": "GOTOWEBINAR_ACCESS_TOKEN not configured"}
    if not organizer_key:
        return {"error": "GOTOWEBINAR_ORGANIZER_KEY not configured"}

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "gotowebinar_list_webinars":
                params: dict[str, Any] = {}
                if "from_time" in arguments:
                    params["fromTime"] = arguments["from_time"]
                if "to_time" in arguments:
                    params["toTime"] = arguments["to_time"]
                if "page" in arguments:
                    params["page"] = arguments["page"]
                if "size" in arguments:
                    params["size"] = arguments["size"]
                r = await client.get(
                    f"{BASE_URL}/organizers/{organizer_key}/webinars",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gotowebinar_create_webinar":
                payload: dict[str, Any] = {
                    "subject": arguments["subject"],
                    "times": arguments["times"],
                }
                if "description" in arguments:
                    payload["description"] = arguments["description"]
                if "timezone" in arguments:
                    payload["timeZone"] = arguments["timezone"]
                r = await client.post(
                    f"{BASE_URL}/organizers/{organizer_key}/webinars",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gotowebinar_register_attendee":
                webinar_key = arguments["webinar_key"]
                r = await client.post(
                    f"{BASE_URL}/organizers/{organizer_key}/webinars/{webinar_key}/registrants",
                    headers=headers,
                    json={
                        "firstName": arguments["first_name"],
                        "lastName": arguments["last_name"],
                        "email": arguments["email"],
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gotowebinar_list_registrants":
                webinar_key = arguments["webinar_key"]
                params = {}
                if "page" in arguments:
                    params["page"] = arguments["page"]
                if "size" in arguments:
                    params["size"] = arguments["size"]
                r = await client.get(
                    f"{BASE_URL}/organizers/{organizer_key}/webinars/{webinar_key}/registrants",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gotowebinar_get_performance":
                webinar_key = arguments["webinar_key"]
                r = await client.get(
                    f"{BASE_URL}/organizers/{organizer_key}/webinars/{webinar_key}/performance",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "gotowebinar_cancel_webinar":
                webinar_key = arguments["webinar_key"]
                params = {"sendCancellationEmails": str(arguments.get("send_cancellation_emails", True)).lower()}
                r = await client.delete(
                    f"{BASE_URL}/organizers/{organizer_key}/webinars/{webinar_key}",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return {"cancelled": True, "webinar_key": webinar_key}

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
