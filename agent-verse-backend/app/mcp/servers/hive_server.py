"""Hive MCP server — project and action management, workspaces, and collaboration.

Environment:
  HIVE_API_KEY: Hive API key
  HIVE_USER_ID: Hive user ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://app.hive.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "hive_list_actions",
        "description": "List actions (tasks) in a Hive project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Hive project ID"},
                "status": {"type": "string", "description": "Filter by status"},
                "assignee_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "hive_create_action",
        "description": "Create a new action (task) in Hive",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "assignees": {"type": "array", "items": {"type": "string"}, "description": "User IDs"},
                "due_date": {"type": "string", "description": "ISO 8601 datetime"},
                "priority": {"type": "string", "enum": ["none", "low", "medium", "high", "urgent"]},
            },
            "required": ["project_id", "title"],
        },
    },
    {
        "name": "hive_list_projects",
        "description": "List all Hive projects the user has access to",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
            },
        },
    },
    {
        "name": "hive_update_action_status",
        "description": "Update the status of an action",
        "parameters": {
            "type": "object",
            "properties": {
                "action_id": {"type": "string"},
                "status": {"type": "string", "description": "New status value"},
            },
            "required": ["action_id", "status"],
        },
    },
    {
        "name": "hive_list_workspaces",
        "description": "List all Hive workspaces for the user",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "hive_add_comment",
        "description": "Add a comment to a Hive action",
        "parameters": {
            "type": "object",
            "properties": {
                "action_id": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["action_id", "message"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("HIVE_API_KEY", "")
    user_id = os.getenv("HIVE_USER_ID", "")
    if not api_key or not user_id:
        return {"error": "HIVE_API_KEY and HIVE_USER_ID must be configured"}

    headers = {
        "api_key": api_key,
        "user_id": user_id,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "hive_list_actions":
                pid = arguments["project_id"]
                params: dict[str, Any] = {}
                if status := arguments.get("status"):
                    params["status"] = status
                if aid := arguments.get("assignee_id"):
                    params["assignee"] = aid
                r = await c.get(f"{BASE}/projects/{pid}/actions", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hive_create_action":
                payload: dict[str, Any] = {
                    "title": arguments["title"],
                    "project_id": arguments["project_id"],
                }
                for field in ("description", "assignees", "due_date", "priority"):
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{BASE}/actions", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hive_list_projects":
                params = {}
                if wsid := arguments.get("workspace_id"):
                    params["workspace_id"] = wsid
                r = await c.get(f"{BASE}/projects", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hive_update_action_status":
                action_id = arguments["action_id"]
                payload = {"status": arguments["status"]}
                r = await c.put(f"{BASE}/actions/{action_id}", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hive_list_workspaces":
                r = await c.get(f"{BASE}/workspaces")
                r.raise_for_status()
                return r.json()

            elif tool_name == "hive_add_comment":
                action_id = arguments["action_id"]
                payload = {"message": arguments["message"]}
                r = await c.post(f"{BASE}/actions/{action_id}/comments", json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("hive_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
