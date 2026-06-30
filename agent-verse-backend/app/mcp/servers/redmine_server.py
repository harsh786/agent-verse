"""Redmine MCP server — issue tracking, projects, users, and time entries.

Environment:
  REDMINE_API_KEY: Redmine API access key
  REDMINE_URL:     Base URL of Redmine instance (e.g. https://redmine.example.com)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "redmine_list_issues",
        "description": "List issues in Redmine with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
                "status_id": {
                    "type": "string",
                    "description": "open, closed, * (all), or a numeric status ID",
                    "default": "open",
                },
                "assigned_to_id": {"type": "integer", "description": "User ID or 'me'"},
                "tracker_id": {"type": "integer"},
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "redmine_create_issue",
        "description": "Create a new issue in Redmine",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "tracker_id": {"type": "integer"},
                "status_id": {"type": "integer"},
                "priority_id": {"type": "integer"},
                "assigned_to_id": {"type": "integer"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["project_id", "subject"],
        },
    },
    {
        "name": "redmine_update_issue",
        "description": "Update an existing Redmine issue",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "integer"},
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "status_id": {"type": "integer"},
                "priority_id": {"type": "integer"},
                "assigned_to_id": {"type": "integer"},
                "notes": {"type": "string", "description": "Add a journal note"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "redmine_list_projects",
        "description": "List all Redmine projects",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "redmine_list_users",
        "description": "List Redmine users (admin required)",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "integer", "description": "0=anonymous, 1=active, 2=registered, 3=locked", "default": 1},
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "redmine_get_time_entries",
        "description": "Get time entries for a project or user",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "user_id": {"type": "integer"},
                "from_date": {"type": "string", "description": "YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "default": 25},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("REDMINE_API_KEY", "")
    base_url = os.getenv("REDMINE_URL", "").rstrip("/")
    if not api_key or not base_url:
        return {"error": "REDMINE_API_KEY and REDMINE_URL must be configured"}

    headers = {
        "X-Redmine-API-Key": api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "redmine_list_issues":
                params: dict[str, Any] = {
                    "status_id": arguments.get("status_id", "open"),
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                for field in ("project_id", "assigned_to_id", "tracker_id"):
                    if v := arguments.get(field):
                        params[field] = v
                r = await c.get(f"{base_url}/issues.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "redmine_create_issue":
                issue: dict[str, Any] = {
                    "project_id": arguments["project_id"],
                    "subject": arguments["subject"],
                }
                for field in ("description", "tracker_id", "status_id", "priority_id", "assigned_to_id", "due_date"):
                    if v := arguments.get(field):
                        issue[field] = v
                r = await c.post(f"{base_url}/issues.json", json={"issue": issue})
                r.raise_for_status()
                return r.json()

            elif tool_name == "redmine_update_issue":
                iid = arguments["issue_id"]
                issue = {}
                for field in ("subject", "description", "status_id", "priority_id", "assigned_to_id", "notes"):
                    if v := arguments.get(field):
                        issue[field] = v
                r = await c.put(f"{base_url}/issues/{iid}.json", json={"issue": issue})
                r.raise_for_status()
                return {"status": "updated"} if r.status_code == 204 else r.json()

            elif tool_name == "redmine_list_projects":
                params = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                r = await c.get(f"{base_url}/projects.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "redmine_list_users":
                params = {
                    "status": arguments.get("status", 1),
                    "limit": arguments.get("limit", 25),
                }
                r = await c.get(f"{base_url}/users.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "redmine_get_time_entries":
                params = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                for field in ("project_id", "user_id"):
                    if v := arguments.get(field):
                        params[field] = v
                if fd := arguments.get("from_date"):
                    params["from"] = fd
                if td := arguments.get("to_date"):
                    params["to"] = td
                r = await c.get(f"{base_url}/time_entries.json", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("redmine_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
