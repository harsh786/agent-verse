"""ClickUp MCP server — ClickUp REST API v2 integration.

Environment variables:
  CLICKUP_API_TOKEN: ClickUp personal API token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

CLICKUP_BASE = "https://api.clickup.com/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "clickup_list_workspaces",
        "description": "List ClickUp workspaces (teams) for the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "clickup_list_spaces",
        "description": "List ClickUp spaces in a workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "Workspace (team) ID"},
                "archived": {"type": "boolean", "default": False},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "clickup_list_lists",
        "description": "List ClickUp lists in a folder or space",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string", "description": "Folder ID (use clickup_list_folders to get IDs)"},
                "space_id": {
                    "type": "string",
                    "description": "Space ID to list folderless lists (alternative to folder_id)",
                },
                "archived": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "clickup_list_tasks",
        "description": "List tasks in a ClickUp list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string"},
                "archived": {"type": "boolean", "default": False},
                "page": {"type": "integer", "default": 0},
                "order_by": {
                    "type": "string",
                    "enum": ["id", "created", "updated", "due_date"],
                    "default": "created",
                },
                "status": {"type": "string", "description": "Filter by status name"},
                "assignees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by assignee user IDs",
                },
                "due_date_gt": {"type": "integer", "description": "Due date greater than (Unix ms)"},
                "due_date_lt": {"type": "integer", "description": "Due date less than (Unix ms)"},
                "include_closed": {"type": "boolean", "default": False},
            },
            "required": ["list_id"],
        },
    },
    {
        "name": "clickup_get_task",
        "description": "Get a single ClickUp task by its ID",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "clickup_create_task",
        "description": "Create a new task in a ClickUp list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "status": {"type": "string"},
                "priority": {
                    "type": "integer",
                    "enum": [1, 2, 3, 4],
                    "description": "1=Urgent, 2=High, 3=Normal, 4=Low",
                },
                "due_date": {"type": "integer", "description": "Due date as Unix timestamp in milliseconds"},
                "due_date_time": {"type": "boolean", "default": False},
                "assignees": {"type": "array", "items": {"type": "integer"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "parent": {"type": "string", "description": "Parent task ID for subtasks"},
                "time_estimate": {"type": "integer", "description": "Time estimate in milliseconds"},
            },
            "required": ["list_id", "name"],
        },
    },
    {
        "name": "clickup_update_task",
        "description": "Update an existing ClickUp task",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "status": {"type": "string"},
                "priority": {"type": "integer", "enum": [1, 2, 3, 4]},
                "due_date": {"type": "integer"},
                "assignees": {
                    "type": "object",
                    "properties": {
                        "add": {"type": "array", "items": {"type": "integer"}},
                        "rem": {"type": "array", "items": {"type": "integer"}},
                    },
                },
                "archived": {"type": "boolean"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "clickup_add_comment",
        "description": "Add a comment to a ClickUp task",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "comment_text": {"type": "string"},
                "notify_all": {"type": "boolean", "default": False},
            },
            "required": ["task_id", "comment_text"],
        },
    },
    {
        "name": "clickup_list_folders",
        "description": "List folders in a ClickUp space",
        "parameters": {
            "type": "object",
            "properties": {
                "space_id": {"type": "string"},
                "archived": {"type": "boolean", "default": False},
            },
            "required": ["space_id"],
        },
    },
]


def _clickup_headers() -> dict[str, str]:
    token = os.getenv("CLICKUP_API_TOKEN", "")
    return {
        "Authorization": token,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("CLICKUP_API_TOKEN", "")
    if not token:
        return {"error": "CLICKUP_API_TOKEN not configured"}

    async with httpx.AsyncClient(
        base_url=CLICKUP_BASE, headers=_clickup_headers(), timeout=30.0
    ) as client:
        if tool_name == "clickup_list_workspaces":
            resp = await client.get("/team")
            resp.raise_for_status()
            return {"workspaces": resp.json().get("teams", [])}

        elif tool_name == "clickup_list_spaces":
            workspace_id = arguments["workspace_id"]
            params = {"archived": str(arguments.get("archived", False)).lower()}
            resp = await client.get(f"/team/{workspace_id}/space", params=params)
            resp.raise_for_status()
            return {"spaces": resp.json().get("spaces", [])}

        elif tool_name == "clickup_list_lists":
            if arguments.get("folder_id"):
                resp = await client.get(
                    f"/folder/{arguments['folder_id']}/list",
                    params={"archived": str(arguments.get("archived", False)).lower()},
                )
            elif arguments.get("space_id"):
                resp = await client.get(
                    f"/space/{arguments['space_id']}/list",
                    params={"archived": str(arguments.get("archived", False)).lower()},
                )
            else:
                return {"error": "Either folder_id or space_id is required"}
            resp.raise_for_status()
            return {"lists": resp.json().get("lists", [])}

        elif tool_name == "clickup_list_tasks":
            list_id = arguments["list_id"]
            params: dict[str, Any] = {
                "archived": str(arguments.get("archived", False)).lower(),
                "page": arguments.get("page", 0),
                "order_by": arguments.get("order_by", "created"),
                "include_closed": str(arguments.get("include_closed", False)).lower(),
            }
            if arguments.get("status"):
                params["statuses[]"] = arguments["status"]
            if arguments.get("assignees"):
                params["assignees[]"] = arguments["assignees"]
            if arguments.get("due_date_gt"):
                params["due_date_gt"] = arguments["due_date_gt"]
            if arguments.get("due_date_lt"):
                params["due_date_lt"] = arguments["due_date_lt"]

            resp = await client.get(f"/list/{list_id}/task", params=params)
            resp.raise_for_status()
            return {"tasks": resp.json().get("tasks", [])}

        elif tool_name == "clickup_get_task":
            resp = await client.get(f"/task/{arguments['task_id']}")
            resp.raise_for_status()
            return {"task": resp.json()}

        elif tool_name == "clickup_create_task":
            list_id = arguments["list_id"]
            payload: dict[str, Any] = {"name": arguments["name"]}
            optional = [
                "description", "status", "priority", "due_date",
                "due_date_time", "assignees", "tags", "parent", "time_estimate",
            ]
            for field in optional:
                if field in arguments and arguments[field] is not None:
                    payload[field] = arguments[field]

            resp = await client.post(f"/list/{list_id}/task", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "task_id": data.get("id", ""),
                "name": data.get("name", ""),
                "url": data.get("url", ""),
                "status": (data.get("status") or {}).get("status", ""),
            }

        elif tool_name == "clickup_update_task":
            task_id = arguments["task_id"]
            payload: dict[str, Any] = {}
            updatable = ["name", "description", "status", "priority", "due_date", "assignees", "archived"]
            for field in updatable:
                if field in arguments:
                    payload[field] = arguments[field]

            resp = await client.put(f"/task/{task_id}", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"task_id": data.get("id", ""), "name": data.get("name", ""), "updated": True}

        elif tool_name == "clickup_add_comment":
            task_id = arguments["task_id"]
            payload = {
                "comment_text": arguments["comment_text"],
                "notify_all": arguments.get("notify_all", False),
            }
            resp = await client.post(f"/task/{task_id}/comment", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"comment_id": data.get("id", ""), "created": True}

        elif tool_name == "clickup_list_folders":
            space_id = arguments["space_id"]
            params = {"archived": str(arguments.get("archived", False)).lower()}
            resp = await client.get(f"/space/{space_id}/folder", params=params)
            resp.raise_for_status()
            return {"folders": resp.json().get("folders", [])}

        else:
            return {"error": f"Unknown tool: {tool_name}"}
