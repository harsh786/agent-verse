"""Microsoft To Do MCP server — task list management via Microsoft Graph API.

Environment:
  MICROSOFT_ACCESS_TOKEN: Microsoft OAuth2 access token with Tasks.ReadWrite scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GRAPH_TODO = "https://graph.microsoft.com/v1.0/me/todo"

TOOL_DEFINITIONS = [
    {
        "name": "todo_list_task_lists",
        "description": "List all task lists in Microsoft To Do",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "todo_list_tasks",
        "description": "List tasks in a To Do task list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Task list ID"},
                "filter": {
                    "type": "string",
                    "description": "OData filter e.g. status eq 'notStarted'",
                },
                "top": {"type": "integer", "default": 50},
            },
            "required": ["list_id"],
        },
    },
    {
        "name": "todo_create_task",
        "description": "Create a new task in a To Do task list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "Task notes/body"},
                "due_date_time": {
                    "type": "string",
                    "description": "ISO8601 datetime for due date",
                },
                "importance": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "default": "normal",
                },
            },
            "required": ["list_id", "title"],
        },
    },
    {
        "name": "todo_complete_task",
        "description": "Mark a To Do task as completed",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["list_id", "task_id"],
        },
    },
    {
        "name": "todo_update_task",
        "description": "Update properties of an existing To Do task",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string"},
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "due_date_time": {"type": "string"},
                "importance": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                },
            },
            "required": ["list_id", "task_id"],
        },
    },
    {
        "name": "todo_create_task_list",
        "description": "Create a new task list in Microsoft To Do",
        "parameters": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string"},
            },
            "required": ["display_name"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("MICROSOFT_ACCESS_TOKEN", "")
    if not token:
        return {"error": "MICROSOFT_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "todo_list_task_lists":
                r = await c.get(f"{GRAPH_TODO}/lists", headers=hdrs)
                r.raise_for_status()
                data = r.json()
                return {
                    "lists": [
                        {
                            "id": lst.get("id"),
                            "display_name": lst.get("displayName"),
                            "is_owner": lst.get("isOwner"),
                            "is_shared": lst.get("isShared"),
                        }
                        for lst in data.get("value", [])
                    ]
                }

            elif tool_name == "todo_list_tasks":
                list_id = arguments["list_id"]
                params: dict[str, Any] = {"$top": arguments.get("top", 50)}
                if arguments.get("filter"):
                    params["$filter"] = arguments["filter"]
                r = await c.get(
                    f"{GRAPH_TODO}/lists/{list_id}/tasks",
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
                            "importance": t.get("importance"),
                            "due_date_time": t.get("dueDateTime", {}).get("dateTime")
                            if t.get("dueDateTime")
                            else None,
                            "completed_date_time": t.get("completedDateTime", {}).get("dateTime")
                            if t.get("completedDateTime")
                            else None,
                        }
                        for t in data.get("value", [])
                    ]
                }

            elif tool_name == "todo_create_task":
                list_id = arguments["list_id"]
                body: dict[str, Any] = {
                    "title": arguments["title"],
                    "importance": arguments.get("importance", "normal"),
                }
                if arguments.get("body"):
                    body["body"] = {"content": arguments["body"], "contentType": "text"}
                if arguments.get("due_date_time"):
                    body["dueDateTime"] = {
                        "dateTime": arguments["due_date_time"],
                        "timeZone": "UTC",
                    }
                r = await c.post(
                    f"{GRAPH_TODO}/lists/{list_id}/tasks",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "created": True,
                }

            elif tool_name == "todo_complete_task":
                r = await c.patch(
                    f"{GRAPH_TODO}/lists/{arguments['list_id']}/tasks/{arguments['task_id']}",
                    headers=hdrs,
                    json={"status": "completed"},
                )
                r.raise_for_status()
                return {
                    "task_id": arguments["task_id"],
                    "status": "completed",
                    "updated": True,
                }

            elif tool_name == "todo_update_task":
                patch: dict[str, Any] = {}
                if arguments.get("title"):
                    patch["title"] = arguments["title"]
                if arguments.get("body"):
                    patch["body"] = {"content": arguments["body"], "contentType": "text"}
                if arguments.get("due_date_time"):
                    patch["dueDateTime"] = {
                        "dateTime": arguments["due_date_time"],
                        "timeZone": "UTC",
                    }
                if arguments.get("importance"):
                    patch["importance"] = arguments["importance"]
                r = await c.patch(
                    f"{GRAPH_TODO}/lists/{arguments['list_id']}/tasks/{arguments['task_id']}",
                    headers=hdrs,
                    json=patch,
                )
                r.raise_for_status()
                return {"task_id": arguments["task_id"], "updated": True}

            elif tool_name == "todo_create_task_list":
                r = await c.post(
                    f"{GRAPH_TODO}/lists",
                    headers=hdrs,
                    json={"displayName": arguments["display_name"]},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "display_name": data.get("displayName"),
                    "created": True,
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("todo_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
