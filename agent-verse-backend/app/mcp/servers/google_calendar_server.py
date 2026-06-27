"""Google Calendar MCP server — events and calendar management via Calendar API v3.

Environment variables (one required):
  GOOGLE_ACCESS_TOKEN:         OAuth2 bearer token
  GOOGLE_SERVICE_ACCOUNT_JSON: JSON string of a service-account key file
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"

TOOL_DEFINITIONS = [
    {
        "name": "calendar_list_events",
        "description": "List upcoming events from a Google Calendar",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "default": "primary",
                    "description": "Calendar ID ('primary' for the main calendar)",
                },
                "time_min": {
                    "type": "string",
                    "description": "RFC3339 datetime; only events after this time (e.g. 2024-01-01T00:00:00Z)",
                },
                "time_max": {
                    "type": "string",
                    "description": "RFC3339 datetime; only events before this time",
                },
                "max_results": {"type": "integer", "default": 20},
                "query": {"type": "string", "description": "Free-text search filter"},
            },
        },
    },
    {
        "name": "calendar_get_event",
        "description": "Get a single calendar event by its ID",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "default": "primary"},
                "event_id": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_create_event",
        "description": "Create a new event on a Google Calendar",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "default": "primary"},
                "summary": {"type": "string", "description": "Event title"},
                "description": {"type": "string"},
                "start": {
                    "type": "object",
                    "description": "Start time object with 'dateTime' (RFC3339) and 'timeZone'",
                },
                "end": {
                    "type": "object",
                    "description": "End time object with 'dateTime' (RFC3339) and 'timeZone'",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"email": {"type": "string"}}},
                },
                "location": {"type": "string"},
            },
            "required": ["summary", "start", "end"],
        },
    },
    {
        "name": "calendar_update_event",
        "description": "Update an existing calendar event",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "default": "primary"},
                "event_id": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "start": {"type": "object"},
                "end": {"type": "object"},
                "attendees": {"type": "array", "items": {"type": "object"}},
                "location": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_delete_event",
        "description": "Delete a calendar event",
        "parameters": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "default": "primary"},
                "event_id": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_list_calendars",
        "description": "List all calendars the user has access to",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "calendar_check_freebusy",
        "description": "Check free/busy times for a set of calendars within a time window",
        "parameters": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "RFC3339 start of window"},
                "time_max": {"type": "string", "description": "RFC3339 end of window"},
                "calendar_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of calendar IDs to check",
                    "default": ["primary"],
                },
            },
            "required": ["time_min", "time_max"],
        },
    },
]


def _google_token() -> str:
    direct = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if direct:
        return direct
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if sa_json:
        try:
            from google.auth.transport.requests import Request  # type: ignore[import]
            from google.oauth2 import service_account  # type: ignore[import]

            creds = service_account.Credentials.from_service_account_info(
                json.loads(sa_json),
                scopes=["https://www.googleapis.com/auth/calendar"],
            )
            creds.refresh(Request())
            return creds.token  # type: ignore[return-value]
        except Exception:
            logger.debug("google_service_account_refresh_failed", exc_info=True)
    return ""


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = _google_token()
    if not token:
        return {"error": "GOOGLE_ACCESS_TOKEN or GOOGLE_SERVICE_ACCOUNT_JSON required"}

    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    cal_id = arguments.get("calendar_id", "primary")

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "calendar_list_events":
                params: dict[str, Any] = {
                    "maxResults": arguments.get("max_results", 20),
                    "singleEvents": True,
                    "orderBy": "startTime",
                }
                if t := arguments.get("time_min"):
                    params["timeMin"] = t
                if t := arguments.get("time_max"):
                    params["timeMax"] = t
                if q := arguments.get("query"):
                    params["q"] = q
                r = await c.get(
                    f"{CALENDAR_BASE}/calendars/{cal_id}/events",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "events": [
                        {
                            "id": e["id"],
                            "summary": e.get("summary", ""),
                            "start": e.get("start", {}),
                            "end": e.get("end", {}),
                            "status": e.get("status", ""),
                            "location": e.get("location", ""),
                        }
                        for e in data.get("items", [])
                    ],
                    "next_page_token": data.get("nextPageToken"),
                }

            elif tool_name == "calendar_get_event":
                event_id = arguments["event_id"]
                r = await c.get(
                    f"{CALENDAR_BASE}/calendars/{cal_id}/events/{event_id}",
                    headers=hdrs,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "calendar_create_event":
                body: dict[str, Any] = {
                    "summary": arguments["summary"],
                    "start": arguments["start"],
                    "end": arguments["end"],
                }
                for optional in ("description", "location", "attendees"):
                    if v := arguments.get(optional):
                        body[optional] = v
                r = await c.post(
                    f"{CALENDAR_BASE}/calendars/{cal_id}/events",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "event_id": data["id"],
                    "html_link": data.get("htmlLink", ""),
                    "summary": data.get("summary", ""),
                    "start": data.get("start", {}),
                    "end": data.get("end", {}),
                }

            elif tool_name == "calendar_update_event":
                event_id = arguments["event_id"]
                # Fetch existing first for a safe PATCH
                r = await c.get(
                    f"{CALENDAR_BASE}/calendars/{cal_id}/events/{event_id}",
                    headers=hdrs,
                )
                r.raise_for_status()
                body = r.json()
                for field in ("summary", "description", "start", "end", "attendees", "location"):
                    if field in arguments:
                        body[field] = arguments[field]
                r = await c.put(
                    f"{CALENDAR_BASE}/calendars/{cal_id}/events/{event_id}",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "calendar_delete_event":
                event_id = arguments["event_id"]
                r = await c.delete(
                    f"{CALENDAR_BASE}/calendars/{cal_id}/events/{event_id}",
                    headers=hdrs,
                )
                return {"success": r.status_code == 204}

            elif tool_name == "calendar_list_calendars":
                r = await c.get(
                    f"{CALENDAR_BASE}/users/me/calendarList",
                    headers=hdrs,
                    params={"maxResults": arguments.get("max_results", 20)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "calendars": [
                        {
                            "id": cal["id"],
                            "summary": cal.get("summary", ""),
                            "access_role": cal.get("accessRole", ""),
                            "primary": cal.get("primary", False),
                        }
                        for cal in data.get("items", [])
                    ]
                }

            elif tool_name == "calendar_check_freebusy":
                cal_ids = arguments.get("calendar_ids", ["primary"])
                body = {
                    "timeMin": arguments["time_min"],
                    "timeMax": arguments["time_max"],
                    "items": [{"id": cid} for cid in cal_ids],
                }
                r = await c.post(
                    f"{CALENDAR_BASE}/freeBusy",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("calendar_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
