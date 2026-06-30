"""Toggl Track MCP server — time tracking, projects, clients, and reports.

Environment:
  TOGGL_API_TOKEN: Toggl Track API token (from Profile Settings)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.track.toggl.com/api/v9"
REPORTS_BASE = "https://api.track.toggl.com/reports/api/v3"

TOOL_DEFINITIONS = [
    {
        "name": "toggl_list_time_entries",
        "description": "List time entries for the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "ISO 8601 start date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "ISO 8601 end date (YYYY-MM-DD)"},
                "meta": {"type": "boolean", "default": False, "description": "Include metadata"},
            },
        },
    },
    {
        "name": "toggl_create_time_entry",
        "description": "Create a new time entry",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "integer", "description": "Workspace ID"},
                "start": {"type": "string", "description": "ISO 8601 start time"},
                "stop": {"type": "string", "description": "ISO 8601 stop time (omit to start running timer)"},
                "duration": {"type": "integer", "description": "Duration in seconds (-1 for running)"},
                "description": {"type": "string"},
                "project_id": {"type": "integer"},
                "billable": {"type": "boolean", "default": False},
            },
            "required": ["workspace_id", "start", "duration"],
        },
    },
    {
        "name": "toggl_stop_timer",
        "description": "Stop the current running time entry",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "integer"},
                "time_entry_id": {"type": "integer"},
            },
            "required": ["workspace_id", "time_entry_id"],
        },
    },
    {
        "name": "toggl_list_projects",
        "description": "List all projects in a workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "integer"},
                "active": {"type": "boolean", "default": True},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "toggl_list_clients",
        "description": "List all clients in a workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["active", "archived"], "default": "active"},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "toggl_get_detailed_report",
        "description": "Get a detailed time report for a workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "integer"},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "project_ids": {"type": "array", "items": {"type": "integer"}},
                "page_size": {"type": "integer", "default": 50},
            },
            "required": ["workspace_id", "start_date", "end_date"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_token = os.getenv("TOGGL_API_TOKEN", "")
    if not api_token:
        return {"error": "TOGGL_API_TOKEN not configured"}

    auth = (api_token, "api_token")

    try:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as c:
            if tool_name == "toggl_list_time_entries":
                params: dict[str, Any] = {}
                if s := arguments.get("start_date"):
                    params["start_date"] = s
                if e := arguments.get("end_date"):
                    params["end_date"] = e
                if arguments.get("meta"):
                    params["meta"] = "true"
                r = await c.get(f"{BASE}/me/time_entries", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "toggl_create_time_entry":
                ws = arguments["workspace_id"]
                payload: dict[str, Any] = {
                    "start": arguments["start"],
                    "duration": arguments["duration"],
                    "workspace_id": ws,
                    "created_with": "agentverse-mcp",
                }
                if stop := arguments.get("stop"):
                    payload["stop"] = stop
                if desc := arguments.get("description"):
                    payload["description"] = desc
                if pid := arguments.get("project_id"):
                    payload["project_id"] = pid
                if arguments.get("billable") is not None:
                    payload["billable"] = arguments["billable"]
                r = await c.post(f"{BASE}/workspaces/{ws}/time_entries", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "toggl_stop_timer":
                ws = arguments["workspace_id"]
                te_id = arguments["time_entry_id"]
                r = await c.patch(f"{BASE}/workspaces/{ws}/time_entries/{te_id}/stop")
                r.raise_for_status()
                return r.json()

            elif tool_name == "toggl_list_projects":
                ws = arguments["workspace_id"]
                params = {"active": str(arguments.get("active", True)).lower()}
                r = await c.get(f"{BASE}/workspaces/{ws}/projects", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "toggl_list_clients":
                ws = arguments["workspace_id"]
                params = {"status": arguments.get("status", "active")}
                r = await c.get(f"{BASE}/workspaces/{ws}/clients", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "toggl_get_detailed_report":
                ws = arguments["workspace_id"]
                payload = {
                    "start_date": arguments["start_date"],
                    "end_date": arguments["end_date"],
                    "page_size": arguments.get("page_size", 50),
                }
                if pids := arguments.get("project_ids"):
                    payload["project_ids"] = pids
                r = await c.post(
                    f"{REPORTS_BASE}/workspace/{ws}/search/time_entries",
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("toggl_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
