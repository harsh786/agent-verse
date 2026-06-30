"""Eventbrite MCP server — event creation, ticketing, and attendee management.

Environment:
  EVENTBRITE_API_KEY: Eventbrite private token for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://www.eventbriteapi.com/v3"

TOOL_DEFINITIONS = [
    {
        "name": "eventbrite_list_events",
        "description": "List all events for the authenticated organizer with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: draft, live, completed, canceled"},
                "page": {"type": "integer", "description": "Page number for pagination"},
                "page_size": {"type": "integer", "description": "Events per page"},
                "time_filter": {"type": "string", "description": "Filter by time: current, past, future"},
            },
        },
    },
    {
        "name": "eventbrite_create_event",
        "description": "Create a new Eventbrite event with title, date, and venue information",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name/title of the event"},
                "description": {"type": "string", "description": "HTML description of the event"},
                "start_utc": {"type": "string", "description": "Start datetime in UTC (YYYY-MM-DDTHH:MM:SSZ)"},
                "end_utc": {"type": "string", "description": "End datetime in UTC"},
                "timezone": {"type": "string", "description": "Timezone (e.g. America/New_York)"},
                "currency": {"type": "string", "description": "Currency code (e.g. USD)"},
                "organizer_id": {"type": "string", "description": "Organizer ID"},
            },
            "required": ["name", "start_utc", "end_utc", "timezone"],
        },
    },
    {
        "name": "eventbrite_list_attendees",
        "description": "List all attendees registered for a specific event",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Eventbrite event ID"},
                "page": {"type": "integer", "description": "Page number"},
                "status": {"type": "string", "description": "Filter by attendee status"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "eventbrite_get_event",
        "description": "Get details for a specific Eventbrite event by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Eventbrite event ID"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "eventbrite_publish_event",
        "description": "Publish a draft Eventbrite event to make it publicly available",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "ID of the draft event to publish"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "eventbrite_list_orders",
        "description": "List ticket orders for a specific Eventbrite event",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Eventbrite event ID"},
                "page": {"type": "integer", "description": "Page number"},
                "page_size": {"type": "integer", "description": "Orders per page"},
            },
            "required": ["event_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("EVENTBRITE_API_KEY", "")
    if not api_key:
        return {"error": "EVENTBRITE_API_KEY not configured"}

    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "eventbrite_list_events":
                params: dict[str, Any] = {}
                if "status" in arguments:
                    params["status"] = arguments["status"]
                if "page" in arguments:
                    params["page"] = arguments["page"]
                if "page_size" in arguments:
                    params["page_size"] = arguments["page_size"]
                if "time_filter" in arguments:
                    params["time_filter"] = arguments["time_filter"]
                r = await client.get(f"{BASE_URL}/users/me/events/", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "eventbrite_create_event":
                payload = {
                    "event.name.html": arguments["name"],
                    "event.start.utc": arguments["start_utc"],
                    "event.start.timezone": arguments["timezone"],
                    "event.end.utc": arguments["end_utc"],
                    "event.end.timezone": arguments["timezone"],
                    "event.currency": arguments.get("currency", "USD"),
                }
                if "description" in arguments:
                    payload["event.description.html"] = arguments["description"]
                if "organizer_id" in arguments:
                    payload["event.organizer_id"] = arguments["organizer_id"]
                r = await client.post(f"{BASE_URL}/events/", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "eventbrite_list_attendees":
                event_id = arguments["event_id"]
                params = {}
                if "page" in arguments:
                    params["page"] = arguments["page"]
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(
                    f"{BASE_URL}/events/{event_id}/attendees/",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "eventbrite_get_event":
                r = await client.get(
                    f"{BASE_URL}/events/{arguments['event_id']}/",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "eventbrite_publish_event":
                r = await client.post(
                    f"{BASE_URL}/events/{arguments['event_id']}/publish/",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "eventbrite_list_orders":
                event_id = arguments["event_id"]
                params = {}
                if "page" in arguments:
                    params["page"] = arguments["page"]
                if "page_size" in arguments:
                    params["page_size"] = arguments["page_size"]
                r = await client.get(
                    f"{BASE_URL}/events/{event_id}/orders/",
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
