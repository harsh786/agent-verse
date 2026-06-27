"""Calendly MCP server — scheduling, event types, and invitations.

Environment:
  CALENDLY_ACCESS_TOKEN: Personal access token or OAuth token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

CALENDLY_BASE = "https://api.calendly.com"

TOOL_DEFINITIONS = [
    {
        "name": "calendly_get_user",
        "description": "Get the authenticated Calendly user's profile",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "calendly_list_event_types",
        "description": "List event types (meeting types) for a Calendly user or organization",
        "parameters": {
            "type": "object",
            "properties": {
                "user": {
                    "type": "string",
                    "description": "User URI (uses authenticated user if not provided)",
                },
                "organization": {
                    "type": "string",
                    "description": "Organization URI to filter by",
                },
                "active": {"type": "boolean", "description": "Filter by active/inactive status"},
                "count": {"type": "integer", "default": 20},
                "page_token": {"type": "string"},
            },
        },
    },
    {
        "name": "calendly_list_scheduled_events",
        "description": "List scheduled meetings/events",
        "parameters": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "User URI"},
                "organization": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "canceled"]},
                "min_start_time": {"type": "string", "description": "ISO 8601 datetime"},
                "max_start_time": {"type": "string", "description": "ISO 8601 datetime"},
                "count": {"type": "integer", "default": 20},
                "page_token": {"type": "string"},
                "sort": {
                    "type": "string",
                    "enum": ["start_time:asc", "start_time:desc"],
                    "default": "start_time:asc",
                },
            },
        },
    },
    {
        "name": "calendly_get_event",
        "description": "Get details about a specific Calendly scheduled event",
        "parameters": {
            "type": "object",
            "properties": {
                "uuid": {"type": "string", "description": "Event UUID"},
            },
            "required": ["uuid"],
        },
    },
    {
        "name": "calendly_cancel_event",
        "description": "Cancel a Calendly scheduled event",
        "parameters": {
            "type": "object",
            "properties": {
                "uuid": {"type": "string", "description": "Event UUID"},
                "reason": {"type": "string", "description": "Cancellation reason"},
            },
            "required": ["uuid"],
        },
    },
    {
        "name": "calendly_get_invitees",
        "description": "Get invitee details for a scheduled event",
        "parameters": {
            "type": "object",
            "properties": {
                "event_uuid": {"type": "string"},
                "count": {"type": "integer", "default": 50},
                "page_token": {"type": "string"},
            },
            "required": ["event_uuid"],
        },
    },
    {
        "name": "calendly_create_invite",
        "description": "Create a one-off scheduling link for a specific event type",
        "parameters": {
            "type": "object",
            "properties": {
                "max_event_count": {
                    "type": "integer",
                    "default": 1,
                    "description": "Max number of events that can be scheduled with this link",
                },
                "owner": {"type": "string", "description": "User URI (default: authenticated user)"},
                "owner_type": {
                    "type": "string",
                    "enum": ["EventType", "User"],
                    "default": "EventType",
                },
            },
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("CALENDLY_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("CALENDLY_ACCESS_TOKEN", "")
    if not token:
        return {"error": "CALENDLY_ACCESS_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(base_url=CALENDLY_BASE, headers=_headers(), timeout=30.0) as c:
            # Get current user URI for tools that need it
            async def get_user_uri() -> str:
                if user_arg := arguments.get("user"):
                    return user_arg
                r = await c.get("/users/me")
                r.raise_for_status()
                return r.json()["resource"]["uri"]

            if tool_name == "calendly_get_user":
                r = await c.get("/users/me")
                r.raise_for_status()
                return r.json()

            elif tool_name == "calendly_list_event_types":
                params: dict[str, Any] = {"count": arguments.get("count", 20)}
                if user := arguments.get("user"):
                    params["user"] = user
                if org := arguments.get("organization"):
                    params["organization"] = org
                if active := arguments.get("active") is not None:
                    params["active"] = str(arguments["active"]).lower() if "active" in arguments else None
                if pt := arguments.get("page_token"):
                    params["page_token"] = pt
                # Clean None values
                params = {k: v for k, v in params.items() if v is not None}
                r = await c.get("/event_types", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "calendly_list_scheduled_events":
                params = {
                    "count": arguments.get("count", 20),
                    "sort": arguments.get("sort", "start_time:asc"),
                }
                if user := arguments.get("user"):
                    params["user"] = user
                if org := arguments.get("organization"):
                    params["organization"] = org
                for field in ["status", "min_start_time", "max_start_time", "page_token"]:
                    if v := arguments.get(field):
                        params[field] = v
                r = await c.get("/scheduled_events", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "calendly_get_event":
                r = await c.get(f"/scheduled_events/{arguments['uuid']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "calendly_cancel_event":
                payload: dict[str, Any] = {}
                if reason := arguments.get("reason"):
                    payload["reason"] = reason
                r = await c.post(
                    f"/scheduled_events/{arguments['uuid']}/cancellation", json=payload
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "calendly_get_invitees":
                params = {"count": arguments.get("count", 50)}
                if pt := arguments.get("page_token"):
                    params["page_token"] = pt
                r = await c.get(
                    f"/scheduled_events/{arguments['event_uuid']}/invitees", params=params
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "calendly_create_invite":
                payload = {
                    "max_event_count": arguments.get("max_event_count", 1),
                    "owner_type": arguments.get("owner_type", "EventType"),
                }
                if owner := arguments.get("owner"):
                    payload["owner"] = owner
                r = await c.post("/one_off_event_types", json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("calendly_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
