"""Meetup MCP server — group events, RSVPs, and community management.

Environment:
  MEETUP_ACCESS_TOKEN: OAuth2 access token for Meetup API
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.meetup.com"

TOOL_DEFINITIONS = [
    {
        "name": "meetup_list_groups",
        "description": "List Meetup groups the authenticated user belongs to or organizes",
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "Filter by role: organizer, member, assistant_organizer"},
                "page": {"type": "integer", "description": "Results per page"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "meetup_list_events",
        "description": "List upcoming or past events for a specific Meetup group",
        "parameters": {
            "type": "object",
            "properties": {
                "group_urlname": {"type": "string", "description": "URL name of the Meetup group"},
                "status": {"type": "string", "description": "Filter by status: upcoming, past, cancelled"},
                "page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["group_urlname"],
        },
    },
    {
        "name": "meetup_get_event",
        "description": "Get details of a specific Meetup event including attendance and venue",
        "parameters": {
            "type": "object",
            "properties": {
                "group_urlname": {"type": "string", "description": "URL name of the Meetup group"},
                "event_id": {"type": "string", "description": "ID of the Meetup event"},
            },
            "required": ["group_urlname", "event_id"],
        },
    },
    {
        "name": "meetup_list_members",
        "description": "List members of a Meetup group with optional role filters",
        "parameters": {
            "type": "object",
            "properties": {
                "group_urlname": {"type": "string", "description": "URL name of the Meetup group"},
                "role": {"type": "string", "description": "Filter by member role"},
                "page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["group_urlname"],
        },
    },
    {
        "name": "meetup_rsvp_event",
        "description": "RSVP to a Meetup event (yes or no)",
        "parameters": {
            "type": "object",
            "properties": {
                "group_urlname": {"type": "string", "description": "URL name of the group"},
                "event_id": {"type": "string", "description": "ID of the event"},
                "rsvp": {"type": "string", "description": "RSVP response: yes or no"},
                "guests": {"type": "integer", "description": "Number of guests (0-2)"},
            },
            "required": ["group_urlname", "event_id", "rsvp"],
        },
    },
    {
        "name": "meetup_create_event",
        "description": "Create a new Meetup event for a group you organize",
        "parameters": {
            "type": "object",
            "properties": {
                "group_urlname": {"type": "string", "description": "URL name of the organizer's group"},
                "name": {"type": "string", "description": "Name of the event"},
                "description": {"type": "string", "description": "Event description (HTML allowed)"},
                "time": {"type": "integer", "description": "Event start time as Unix timestamp in milliseconds"},
                "duration": {"type": "integer", "description": "Duration in milliseconds"},
                "venue_id": {"type": "string", "description": "Meetup venue ID"},
                "rsvp_limit": {"type": "integer", "description": "Maximum RSVPs allowed"},
            },
            "required": ["group_urlname", "name", "time"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("MEETUP_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "MEETUP_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "meetup_list_groups":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/self/groups", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "meetup_list_events":
                group = arguments["group_urlname"]
                params = {k: v for k, v in arguments.items() if k != "group_urlname" and v is not None}
                r = await client.get(f"{BASE_URL}/{group}/events", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "meetup_get_event":
                r = await client.get(
                    f"{BASE_URL}/{arguments['group_urlname']}/events/{arguments['event_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "meetup_list_members":
                group = arguments["group_urlname"]
                params = {k: v for k, v in arguments.items() if k != "group_urlname" and v is not None}
                r = await client.get(f"{BASE_URL}/{group}/members", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "meetup_rsvp_event":
                r = await client.post(
                    f"{BASE_URL}/{arguments['group_urlname']}/events/{arguments['event_id']}/rsvps",
                    headers=headers,
                    json={
                        "response": arguments["rsvp"],
                        "guests": arguments.get("guests", 0),
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "meetup_create_event":
                group = arguments["group_urlname"]
                payload = {
                    "name": arguments["name"],
                    "time": arguments["time"],
                }
                for k in ("description", "duration", "venue_id", "rsvp_limit"):
                    if k in arguments:
                        payload[k] = arguments[k]
                r = await client.post(f"{BASE_URL}/{group}/events", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
