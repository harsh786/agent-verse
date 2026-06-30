"""Livestorm MCP server — webinar and virtual event management.

Environment:
  LIVESTORM_API_KEY: Livestorm API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.livestorm.co/v1"

TOOL_DEFINITIONS = [
    {
        "name": "livestorm_list_events",
        "description": "List all Livestorm events (webinars) with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "filter_status": {"type": "string", "description": "Filter by status: upcoming, past, draft"},
                "page_size": {"type": "integer", "description": "Events per page"},
                "page_number": {"type": "integer", "description": "Page number for pagination"},
            },
        },
    },
    {
        "name": "livestorm_create_event",
        "description": "Create a new Livestorm event (webinar or online meeting)",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title of the event"},
                "slug": {"type": "string", "description": "URL slug for the event registration page"},
                "description": {"type": "string", "description": "Description of the event"},
                "estimated_duration": {"type": "integer", "description": "Estimated duration in minutes"},
                "language": {"type": "string", "description": "Event language code (e.g. en)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "livestorm_list_registrants",
        "description": "List all registrants for a specific Livestorm event",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "ID of the Livestorm event"},
                "page_size": {"type": "integer", "description": "Registrants per page"},
                "page_number": {"type": "integer", "description": "Page number"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "livestorm_register_attendee",
        "description": "Register a new attendee for a Livestorm event",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "ID of the event to register for"},
                "session_id": {"type": "string", "description": "ID of the specific session"},
                "email": {"type": "string", "description": "Attendee email address"},
                "first_name": {"type": "string", "description": "Attendee first name"},
                "last_name": {"type": "string", "description": "Attendee last name"},
            },
            "required": ["event_id", "session_id", "email"],
        },
    },
    {
        "name": "livestorm_get_event_stats",
        "description": "Get statistics and attendance metrics for a Livestorm event",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "ID of the Livestorm event"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "livestorm_list_sessions",
        "description": "List all sessions (scheduled instances) for a Livestorm event",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "ID of the Livestorm event"},
                "filter_status": {"type": "string", "description": "Filter by session status"},
                "page_size": {"type": "integer", "description": "Sessions per page"},
            },
            "required": ["event_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("LIVESTORM_API_KEY", "")
    if not api_key:
        return {"error": "LIVESTORM_API_KEY not configured"}

    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "livestorm_list_events":
                params: dict[str, Any] = {}
                if "filter_status" in arguments:
                    params["filter[status]"] = arguments["filter_status"]
                if "page_size" in arguments:
                    params["page[size]"] = arguments["page_size"]
                if "page_number" in arguments:
                    params["page[number]"] = arguments["page_number"]
                r = await client.get(f"{BASE_URL}/events", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "livestorm_create_event":
                attributes: dict[str, Any] = {"title": arguments["title"]}
                if "slug" in arguments:
                    attributes["slug"] = arguments["slug"]
                if "description" in arguments:
                    attributes["description"] = arguments["description"]
                if "estimated_duration" in arguments:
                    attributes["estimated_duration"] = arguments["estimated_duration"]
                if "language" in arguments:
                    attributes["language"] = arguments["language"]
                r = await client.post(
                    f"{BASE_URL}/events",
                    headers=headers,
                    json={"data": {"attributes": attributes}},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "livestorm_list_registrants":
                event_id = arguments["event_id"]
                params = {}
                if "page_size" in arguments:
                    params["page[size]"] = arguments["page_size"]
                if "page_number" in arguments:
                    params["page[number]"] = arguments["page_number"]
                r = await client.get(
                    f"{BASE_URL}/events/{event_id}/registrants",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "livestorm_register_attendee":
                event_id = arguments["event_id"]
                r = await client.post(
                    f"{BASE_URL}/events/{event_id}/registrants",
                    headers=headers,
                    json={
                        "data": {
                            "attributes": {
                                "fields": [
                                    {"id": "email", "value": arguments["email"]},
                                    {"id": "first_name", "value": arguments.get("first_name", "")},
                                    {"id": "last_name", "value": arguments.get("last_name", "")},
                                ],
                                "session_id": arguments["session_id"],
                            }
                        }
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "livestorm_get_event_stats":
                event_id = arguments["event_id"]
                r = await client.get(f"{BASE_URL}/events/{event_id}", headers=headers)
                r.raise_for_status()
                return r.json()

            if tool_name == "livestorm_list_sessions":
                event_id = arguments["event_id"]
                params = {}
                if "filter_status" in arguments:
                    params["filter[status]"] = arguments["filter_status"]
                if "page_size" in arguments:
                    params["page[size]"] = arguments["page_size"]
                r = await client.get(
                    f"{BASE_URL}/events/{event_id}/sessions",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
