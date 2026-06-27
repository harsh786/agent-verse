"""Zoom MCP server — meetings, recordings, and user management.

Environment:
  ZOOM_OAUTH_TOKEN: OAuth 2.0 Bearer token (or ZOOM_JWT_TOKEN for legacy)
  ZOOM_JWT_TOKEN:   Legacy JWT token (deprecated, use OAuth)
  ZOOM_ACCOUNT_ID:  Account ID for Server-to-Server OAuth
  ZOOM_CLIENT_ID:   Client ID for Server-to-Server OAuth
  ZOOM_CLIENT_SECRET: Client secret for Server-to-Server OAuth
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

ZOOM_BASE = "https://api.zoom.us/v2"

TOOL_DEFINITIONS = [
    {
        "name": "zoom_list_meetings",
        "description": "List upcoming and scheduled Zoom meetings for the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["scheduled", "live", "upcoming", "upcoming_meetings", "previous_meetings"],
                    "default": "upcoming",
                },
                "page_size": {"type": "integer", "default": 30},
                "next_page_token": {"type": "string"},
                "user_id": {"type": "string", "default": "me"},
            },
        },
    },
    {
        "name": "zoom_create_meeting",
        "description": "Create a new Zoom meeting",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Meeting topic/title"},
                "start_time": {
                    "type": "string",
                    "description": "Meeting start time in ISO 8601 format (UTC), e.g. '2024-01-15T14:00:00Z'",
                },
                "duration": {"type": "integer", "description": "Duration in minutes", "default": 60},
                "agenda": {"type": "string"},
                "password": {"type": "string"},
                "type": {
                    "type": "integer",
                    "description": "1=Instant, 2=Scheduled, 3=Recurring(no fixed time), 8=Recurring(fixed time)",
                    "default": 2,
                },
                "timezone": {"type": "string", "default": "UTC"},
                "settings": {
                    "type": "object",
                    "description": "Meeting settings (host_video, participant_video, waiting_room, etc.)",
                },
                "user_id": {"type": "string", "default": "me"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "zoom_get_meeting",
        "description": "Get details about a Zoom meeting by meeting ID",
        "parameters": {
            "type": "object",
            "properties": {
                "meeting_id": {"type": "string"},
            },
            "required": ["meeting_id"],
        },
    },
    {
        "name": "zoom_update_meeting",
        "description": "Update an existing Zoom meeting",
        "parameters": {
            "type": "object",
            "properties": {
                "meeting_id": {"type": "string"},
                "topic": {"type": "string"},
                "start_time": {"type": "string"},
                "duration": {"type": "integer"},
                "agenda": {"type": "string"},
                "password": {"type": "string"},
                "settings": {"type": "object"},
            },
            "required": ["meeting_id"],
        },
    },
    {
        "name": "zoom_delete_meeting",
        "description": "Delete or cancel a Zoom meeting",
        "parameters": {
            "type": "object",
            "properties": {
                "meeting_id": {"type": "string"},
                "notify_registrants": {"type": "boolean", "default": True},
            },
            "required": ["meeting_id"],
        },
    },
    {
        "name": "zoom_list_recordings",
        "description": "List cloud recordings for a user",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "default": "me"},
                "from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to": {"type": "string", "description": "End date YYYY-MM-DD"},
                "page_size": {"type": "integer", "default": 30},
                "next_page_token": {"type": "string"},
            },
        },
    },
    {
        "name": "zoom_list_users",
        "description": "List users in a Zoom account",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "inactive", "pending"],
                    "default": "active",
                },
                "page_size": {"type": "integer", "default": 30},
                "next_page_token": {"type": "string"},
            },
        },
    },
]


async def _get_token() -> str:
    """Get OAuth token, supporting both direct token and Server-to-Server OAuth."""
    if token := os.getenv("ZOOM_OAUTH_TOKEN") or os.getenv("ZOOM_JWT_TOKEN"):
        return token

    account_id = os.getenv("ZOOM_ACCOUNT_ID", "")
    client_id = os.getenv("ZOOM_CLIENT_ID", "")
    client_secret = os.getenv("ZOOM_CLIENT_SECRET", "")

    if all([account_id, client_id, client_secret]):
        import base64
        creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                "https://zoom.us/oauth/token",
                params={"grant_type": "account_credentials", "account_id": account_id},
                headers={"Authorization": f"Basic {creds}"},
            )
            r.raise_for_status()
            return r.json().get("access_token", "")

    return ""


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        token = await _get_token()
    except Exception as exc:
        return {"error": f"Failed to obtain Zoom token: {exc}"}

    if not token:
        return {
            "error": "Zoom credentials not configured. Set ZOOM_OAUTH_TOKEN or "
            "ZOOM_ACCOUNT_ID + ZOOM_CLIENT_ID + ZOOM_CLIENT_SECRET"
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(base_url=ZOOM_BASE, headers=headers, timeout=30.0) as c:
            if tool_name == "zoom_list_meetings":
                uid = arguments.get("user_id", "me")
                params: dict[str, Any] = {
                    "type": arguments.get("type", "upcoming"),
                    "page_size": arguments.get("page_size", 30),
                }
                if npt := arguments.get("next_page_token"):
                    params["next_page_token"] = npt
                r = await c.get(f"/users/{uid}/meetings", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoom_create_meeting":
                uid = arguments.get("user_id", "me")
                payload: dict[str, Any] = {
                    "topic": arguments["topic"],
                    "type": arguments.get("type", 2),
                    "duration": arguments.get("duration", 60),
                    "timezone": arguments.get("timezone", "UTC"),
                }
                for field in ["start_time", "agenda", "password", "settings"]:
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"/users/{uid}/meetings", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoom_get_meeting":
                r = await c.get(f"/meetings/{arguments['meeting_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoom_update_meeting":
                mid = arguments["meeting_id"]
                payload = {}
                for field in ["topic", "start_time", "duration", "agenda", "password", "settings"]:
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.patch(f"/meetings/{mid}", json=payload)
                r.raise_for_status()
                return {"success": True, "status_code": r.status_code}

            elif tool_name == "zoom_delete_meeting":
                mid = arguments["meeting_id"]
                params = {"notify_registrants": str(arguments.get("notify_registrants", True)).lower()}
                r = await c.delete(f"/meetings/{mid}", params=params)
                r.raise_for_status()
                return {"success": True, "status_code": r.status_code}

            elif tool_name == "zoom_list_recordings":
                uid = arguments.get("user_id", "me")
                params = {"page_size": arguments.get("page_size", 30)}
                if from_date := arguments.get("from"):
                    params["from"] = from_date
                if to_date := arguments.get("to"):
                    params["to"] = to_date
                if npt := arguments.get("next_page_token"):
                    params["next_page_token"] = npt
                r = await c.get(f"/users/{uid}/recordings", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoom_list_users":
                params = {
                    "status": arguments.get("status", "active"),
                    "page_size": arguments.get("page_size", 30),
                }
                if npt := arguments.get("next_page_token"):
                    params["next_page_token"] = npt
                r = await c.get("/users", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("zoom_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
