"""Google Meet MCP server — create and manage video meetings.

Environment:
  GOOGLE_ACCESS_TOKEN: OAuth2 access token with meetings.conference.create scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://meet.googleapis.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "google_meet_create_meeting",
        "description": "Create a new Google Meet conference with a generated meeting URI",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title or topic of the meeting"},
                "start_time": {"type": "string", "description": "Start time in ISO 8601 format"},
                "end_time": {"type": "string", "description": "End time in ISO 8601 format"},
                "timezone": {"type": "string", "description": "Timezone (e.g. America/New_York)"},
            },
        },
    },
    {
        "name": "google_meet_list_meetings",
        "description": "List upcoming and past Google Meet meetings for the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Maximum number of meetings"},
                "page_token": {"type": "string", "description": "Pagination token"},
            },
        },
    },
    {
        "name": "google_meet_get_meeting",
        "description": "Get details of a specific Google Meet meeting including join info",
        "parameters": {
            "type": "object",
            "properties": {
                "meeting_code": {"type": "string", "description": "Meeting code or conference ID"},
            },
            "required": ["meeting_code"],
        },
    },
    {
        "name": "google_meet_end_meeting",
        "description": "End an active Google Meet meeting (remove all participants)",
        "parameters": {
            "type": "object",
            "properties": {
                "conference_record_name": {"type": "string", "description": "Conference record name (conferenceRecords/*)"},
            },
            "required": ["conference_record_name"],
        },
    },
    {
        "name": "google_meet_list_participants",
        "description": "List participants in a Google Meet conference record",
        "parameters": {
            "type": "object",
            "properties": {
                "conference_record_name": {"type": "string", "description": "Conference record name"},
                "page_size": {"type": "integer", "description": "Maximum participants to return"},
                "page_token": {"type": "string", "description": "Pagination token"},
            },
            "required": ["conference_record_name"],
        },
    },
    {
        "name": "google_meet_get_recording",
        "description": "Get recording details for a Google Meet conference session",
        "parameters": {
            "type": "object",
            "properties": {
                "conference_record_name": {"type": "string", "description": "Conference record name"},
                "recording_name": {"type": "string", "description": "Specific recording resource name"},
            },
            "required": ["conference_record_name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "GOOGLE_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "google_meet_create_meeting":
                payload: dict[str, Any] = {}
                if "title" in arguments:
                    payload["title"] = arguments["title"]
                if "start_time" in arguments or "end_time" in arguments:
                    payload["startTime"] = arguments.get("start_time")
                    payload["endTime"] = arguments.get("end_time")
                r = await client.post(f"{BASE_URL}/spaces", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "google_meet_list_meetings":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/conferenceRecords", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "google_meet_get_meeting":
                r = await client.get(
                    f"{BASE_URL}/spaces/{arguments['meeting_code']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_meet_end_meeting":
                r = await client.post(
                    f"{BASE_URL}/{arguments['conference_record_name']}:endActiveConference",
                    headers=headers,
                )
                r.raise_for_status()
                return {"ended": True}

            if tool_name == "google_meet_list_participants":
                conf = arguments["conference_record_name"]
                params = {k: v for k, v in arguments.items() if k != "conference_record_name" and v is not None}
                r = await client.get(
                    f"{BASE_URL}/{conf}/participants",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_meet_get_recording":
                conf = arguments["conference_record_name"]
                if "recording_name" in arguments:
                    r = await client.get(
                        f"{BASE_URL}/{arguments['recording_name']}",
                        headers=headers,
                    )
                else:
                    r = await client.get(
                        f"{BASE_URL}/{conf}/recordings",
                        headers=headers,
                    )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
