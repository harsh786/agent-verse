"""Google Tasks MCP server — task list and task management via Google Tasks API v1.

Environment:
  GOOGLE_ACCESS_TOKEN: OAuth2 bearer token with tasks scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TASKS_BASE = "https://tasks.googleapis.com/tasks/v1"

TOOL_DEFINITIONS = [
    {
        "name": "gtasks_list_task_lists",
        "description": "List all task lists in Google Tasks",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "gtasks_list_tasks",
        "description": "List tasks in a Google Tasks list",
        "parameters": {
            "type": "object",
            "properties": {
                "tasklist_id": {"type": "string", "description": "Task list ID (@default for default)"},
                "show_completed": {"type": "boolean", "default": False},
                "show_hidden": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "default": 100},
                "due_min": {"type": "string", "description": "ISO8601 lower bound for due date"},
                "due_max": {"type": "string", "description": "ISO8601 upper bound for due date"},
            },
            "required": ["tasklist_id"],
        },
    },
    {
        "name": "gtasks_create_task",
        "description": "Create a new task in a Google Tasks list",
        "parameters": {
            "type": "object",
            "properties": {
                "tasklist_id": {"type": "string"},
                "title": {"type": "string"},
                "notes": {"type": "string", "description": "Task notes"},
                "due": {
                    "type": "string",
                    "description": "RFC 3339 due date e.g. 2024-12-31T00:00:00.000Z",
                },
                "parent": {"type": "string", "description": "Parent task ID for subtasks"},
            },
            "required": ["tasklist_id", "title"],
        },
    },
    {
        "name": "gtasks_complete_task",
        "description": "Mark a Google Task as completed",
        "parameters": {
            "type": "object",
            "properties": {
                "tasklist_id": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["tasklist_id", "task_id"],
        },
    },
    {
        "name": "gtasks_update_task",
        "description": "Update the title, notes, or due date of a Google Task",
        "parameters": {
            "type": "object",
            "properties": {
                "tasklist_id": {"type": "string"},
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "notes": {"type": "string"},
                "due": {"type": "string", "description": "RFC 3339 due date"},
                "status": {
                    "type": "string",
                    "enum": ["needsAction", "completed"],
                },
            },
            "required": ["tasklist_id", "task_id"],
        },
    },
    {
        "name": "gtasks_delete_task",
        "description": "Delete a task from a Google Tasks list",
        "parameters": {
            "type": "object",
            "properties": {
                "tasklist_id": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["tasklist_id", "task_id"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if not token:
        return {"error": "GOOGLE_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "gtasks_list_task_lists":
                r = await c.get(
                    f"{TASKS_BASE}/users/@me/lists",
                    headers=hdrs,
                    params={"maxResults": arguments.get("max_results", 20)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "task_lists": [
                        {
                            "id": tl.get("id"),
                            "title": tl.get("title"),
                            "updated": tl.get("updated"),
                        }
                        for tl in data.get("items", [])
                    ]
                }

            elif tool_name == "gtasks_list_tasks":
                tasklist_id = arguments["tasklist_id"]
                params: dict[str, Any] = {
                    "maxResults": arguments.get("max_results", 100),
                    "showCompleted": arguments.get("show_completed", False),
                    "showHidden": arguments.get("show_hidden", False),
                }
                if arguments.get("due_min"):
                    params["dueMin"] = arguments["due_min"]
                if arguments.get("due_max"):
                    params["dueMax"] = arguments["due_max"]
                r = await c.get(
                    f"{TASKS_BASE}/lists/{tasklist_id}/tasks",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "tasks": [
                        {
                            "id": t.get("id"),
                            "title": t.get("title"),
                            "status": t.get("status"),
                            "notes": t.get("notes"),
                            "due": t.get("due"),
                            "updated": t.get("updated"),
                        }
                        for t in data.get("items", [])
                    ]
                }

            elif tool_name == "gtasks_create_task":
                tasklist_id = arguments["tasklist_id"]
                body: dict[str, Any] = {"title": arguments["title"]}
                if arguments.get("notes"):
                    body["notes"] = arguments["notes"]
                if arguments.get("due"):
                    body["due"] = arguments["due"]
                params = {}
                if arguments.get("parent"):
                    params["parent"] = arguments["parent"]
                r = await c.post(
                    f"{TASKS_BASE}/lists/{tasklist_id}/tasks",
                    headers=hdrs,
                    json=body,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "status": data.get("status"),
                    "created": True,
                }

            elif tool_name == "gtasks_complete_task":
                tasklist_id = arguments["tasklist_id"]
                task_id = arguments["task_id"]
                r = await c.patch(
                    f"{TASKS_BASE}/lists/{tasklist_id}/tasks/{task_id}",
                    headers=hdrs,
                    json={"status": "completed"},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": task_id,
                    "status": data.get("status"),
                    "completed": True,
                }

            elif tool_name == "gtasks_update_task":
                tasklist_id = arguments["tasklist_id"]
                task_id = arguments["task_id"]
                patch: dict[str, Any] = {}
                if arguments.get("title"):
                    patch["title"] = arguments["title"]
                if arguments.get("notes"):
                    patch["notes"] = arguments["notes"]
                if arguments.get("due"):
                    patch["due"] = arguments["due"]
                if arguments.get("status"):
                    patch["status"] = arguments["status"]
                r = await c.patch(
                    f"{TASKS_BASE}/lists/{tasklist_id}/tasks/{task_id}",
                    headers=hdrs,
                    json=patch,
                )
                r.raise_for_status()
                return {"id": task_id, "updated": True}

            elif tool_name == "gtasks_delete_task":
                tasklist_id = arguments["tasklist_id"]
                task_id = arguments["task_id"]
                r = await c.delete(
                    f"{TASKS_BASE}/lists/{tasklist_id}/tasks/{task_id}",
                    headers=hdrs,
                )
                r.raise_for_status()
                return {"id": task_id, "deleted": True}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("gtasks_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
