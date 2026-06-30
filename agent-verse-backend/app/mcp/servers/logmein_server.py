"""LogMeIn Rescue MCP server — remote access sessions and computer management.

Environment:
  LOGMEIN_API_KEY: LogMeIn Rescue API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://secure.logmeinrescue.com/API"

TOOL_DEFINITIONS = [
    {
        "name": "logmein_list_sessions",
        "description": "List remote support sessions in LogMeIn Rescue",
        "parameters": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "to": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                "status": {"type": "string", "description": "Filter by session status"},
            },
        },
    },
    {
        "name": "logmein_create_session",
        "description": "Create a new remote support session and get a connection link",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Session name or customer name"},
                "channel_id": {"type": "string", "description": "Support channel ID to assign session to"},
                "notes": {"type": "string", "description": "Session notes"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "logmein_get_session_recording",
        "description": "Get or download the recording of a completed support session",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID to get recording for"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "logmein_list_computers",
        "description": "List computers registered for unattended remote access",
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "Filter by computer group ID"},
                "status": {"type": "string", "description": "Filter by online status"},
            },
        },
    },
    {
        "name": "logmein_get_user_stats",
        "description": "Get support performance statistics for a technician",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Technician user ID"},
                "from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "logmein_end_session",
        "description": "Forcefully end an active remote support session",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID to terminate"},
            },
            "required": ["session_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("LOGMEIN_API_KEY", "")
    if not api_key:
        return {"error": "LOGMEIN_API_KEY not configured"}

    headers = {"content-type": "application/json", "Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "logmein_list_sessions":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/API.asmx/GetSessions", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "logmein_create_session":
                payload: dict[str, Any] = {"name": arguments["name"]}
                if "channel_id" in arguments:
                    payload["channelID"] = arguments["channel_id"]
                if "notes" in arguments:
                    payload["notes"] = arguments["notes"]
                r = await client.post(
                    f"{BASE_URL}/API.asmx/CreateSession",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "logmein_get_session_recording":
                r = await client.get(
                    f"{BASE_URL}/API.asmx/GetSessionRecording",
                    headers=headers,
                    params={"sessionID": arguments["session_id"]},
                )
                r.raise_for_status()
                return {"url": r.text, "session_id": arguments["session_id"]}

            if tool_name == "logmein_list_computers":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/API.asmx/GetComputers", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "logmein_get_user_stats":
                r = await client.get(
                    f"{BASE_URL}/API.asmx/GetTechnicianStats",
                    headers=headers,
                    params={k: v for k, v in arguments.items() if v is not None},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "logmein_end_session":
                r = await client.post(
                    f"{BASE_URL}/API.asmx/EndSession",
                    headers=headers,
                    json={"sessionID": arguments["session_id"]},
                )
                r.raise_for_status()
                return {"ended": True}

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
