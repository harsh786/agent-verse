"""Clockify MCP server — time tracking, projects, workspaces, and reports.

Environment:
  CLOCKIFY_API_KEY: Clockify API key (from Profile Settings → API)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.clockify.me/api/v1"
REPORTS_BASE = "https://reports.api.clockify.me/v1"

TOOL_DEFINITIONS = [
    {
        "name": "clockify_list_workspaces",
        "description": "List all Clockify workspaces the API key has access to",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "clockify_list_time_entries",
        "description": "List time entries for a user in a workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "Workspace ID"},
                "user_id": {"type": "string", "description": "User ID"},
                "start": {"type": "string", "description": "ISO 8601 start date filter"},
                "end": {"type": "string", "description": "ISO 8601 end date filter"},
                "page_size": {"type": "integer", "default": 50},
            },
            "required": ["workspace_id", "user_id"],
        },
    },
    {
        "name": "clockify_create_time_entry",
        "description": "Create a new time entry in a workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "start": {"type": "string", "description": "ISO 8601 start time (e.g. 2024-01-15T09:00:00Z)"},
                "end": {"type": "string", "description": "ISO 8601 end time"},
                "project_id": {"type": "string"},
                "description": {"type": "string"},
                "billable": {"type": "boolean", "default": False},
            },
            "required": ["workspace_id", "start"],
        },
    },
    {
        "name": "clockify_list_projects",
        "description": "List all projects in a Clockify workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "archived": {"type": "boolean", "default": False},
                "page_size": {"type": "integer", "default": 50},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "clockify_list_users",
        "description": "List all users in a Clockify workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "status": {"type": "string", "enum": ["ACTIVE", "INACTIVE", "PENDING"], "default": "ACTIVE"},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "clockify_get_summary_report",
        "description": "Get a summary report for a workspace within a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "start": {"type": "string", "description": "ISO 8601 start datetime"},
                "end": {"type": "string", "description": "ISO 8601 end datetime"},
                "group_by": {
                    "type": "string",
                    "enum": ["PROJECT", "USER", "TAG", "DATE"],
                    "default": "PROJECT",
                },
            },
            "required": ["workspace_id", "start", "end"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("CLOCKIFY_API_KEY", "")
    if not api_key:
        return {"error": "CLOCKIFY_API_KEY not configured"}

    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "clockify_list_workspaces":
                r = await c.get(f"{BASE}/workspaces")
                r.raise_for_status()
                return r.json()

            elif tool_name == "clockify_list_time_entries":
                ws = arguments["workspace_id"]
                uid = arguments["user_id"]
                params: dict[str, Any] = {"page-size": arguments.get("page_size", 50)}
                if s := arguments.get("start"):
                    params["start"] = s
                if e := arguments.get("end"):
                    params["end"] = e
                r = await c.get(f"{BASE}/workspaces/{ws}/user/{uid}/time-entries", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "clockify_create_time_entry":
                ws = arguments["workspace_id"]
                payload: dict[str, Any] = {
                    "start": arguments["start"],
                    "billable": arguments.get("billable", False),
                }
                if end := arguments.get("end"):
                    payload["end"] = end
                if pid := arguments.get("project_id"):
                    payload["projectId"] = pid
                if desc := arguments.get("description"):
                    payload["description"] = desc
                r = await c.post(f"{BASE}/workspaces/{ws}/time-entries", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "clockify_list_projects":
                ws = arguments["workspace_id"]
                params = {
                    "archived": str(arguments.get("archived", False)).lower(),
                    "page-size": arguments.get("page_size", 50),
                }
                r = await c.get(f"{BASE}/workspaces/{ws}/projects", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "clockify_list_users":
                ws = arguments["workspace_id"]
                params = {"status": arguments.get("status", "ACTIVE")}
                r = await c.get(f"{BASE}/workspaces/{ws}/users", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "clockify_get_summary_report":
                ws = arguments["workspace_id"]
                payload = {
                    "dateRangeStart": arguments["start"],
                    "dateRangeEnd": arguments["end"],
                    "summaryFilter": {"groups": [arguments.get("group_by", "PROJECT")]},
                }
                r = await c.post(
                    f"{REPORTS_BASE}/workspaces/{ws}/reports/summary",
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("clockify_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
