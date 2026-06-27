"""Asana MCP server — Asana REST API v1.0 integration.

Environment variables:
  ASANA_ACCESS_TOKEN: Personal access token or OAuth access token
  ASANA_WORKSPACE_GID: Default workspace GID (numeric string)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

ASANA_BASE = "https://app.asana.com/api/1.0"

TOOL_DEFINITIONS = [
    {
        "name": "asana_list_tasks",
        "description": "List tasks in an Asana project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_gid": {"type": "string", "description": "Project GID"},
                "completed_since": {
                    "type": "string",
                    "description": "ISO 8601 datetime — only return tasks completed after this time",
                },
                "opt_fields": {
                    "type": "string",
                    "default": "name,completed,due_on,assignee.name,notes",
                    "description": "Comma-separated fields to include",
                },
            },
            "required": ["project_gid"],
        },
    },
    {
        "name": "asana_get_task",
        "description": "Get details of a single Asana task",
        "parameters": {
            "type": "object",
            "properties": {
                "task_gid": {"type": "string"},
                "opt_fields": {
                    "type": "string",
                    "default": "name,notes,completed,due_on,assignee.name,projects.name,tags.name,custom_fields",
                },
            },
            "required": ["task_gid"],
        },
    },
    {
        "name": "asana_create_task",
        "description": "Create a new task in Asana",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Task name"},
                "notes": {"type": "string", "description": "Task description", "default": ""},
                "project_gid": {"type": "string", "description": "Project to add the task to"},
                "assignee": {"type": "string", "description": "Assignee user GID or 'me'"},
                "due_on": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                "workspace": {"type": "string", "description": "Workspace GID (uses env default if omitted)"},
                "parent": {"type": "string", "description": "Parent task GID for subtasks"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "asana_update_task",
        "description": "Update an existing Asana task",
        "parameters": {
            "type": "object",
            "properties": {
                "task_gid": {"type": "string"},
                "name": {"type": "string"},
                "notes": {"type": "string"},
                "completed": {"type": "boolean"},
                "due_on": {"type": "string"},
                "assignee": {"type": "string"},
            },
            "required": ["task_gid"],
        },
    },
    {
        "name": "asana_list_projects",
        "description": "List Asana projects in the configured workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_gid": {
                    "type": "string",
                    "description": "Workspace GID. Uses ASANA_WORKSPACE_GID env var if omitted.",
                },
                "archived": {"type": "boolean", "default": False},
                "opt_fields": {
                    "type": "string",
                    "default": "name,archived,created_at,owner.name",
                },
            },
        },
    },
    {
        "name": "asana_create_project",
        "description": "Create a new project in Asana",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "workspace_gid": {"type": "string"},
                "notes": {"type": "string", "default": ""},
                "privacy_setting": {
                    "type": "string",
                    "enum": ["public_to_workspace", "private_to_team", "private"],
                    "default": "public_to_workspace",
                },
                "color": {
                    "type": "string",
                    "description": "Project color, e.g. light-green, dark-blue",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "asana_add_comment",
        "description": "Add a comment (story) to an Asana task",
        "parameters": {
            "type": "object",
            "properties": {
                "task_gid": {"type": "string"},
                "text": {"type": "string", "description": "Comment text"},
            },
            "required": ["task_gid", "text"],
        },
    },
]


def _asana_headers() -> dict[str, str]:
    token = os.getenv("ASANA_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("ASANA_ACCESS_TOKEN", "")
    if not token:
        return {"error": "ASANA_ACCESS_TOKEN not configured"}

    workspace_gid = arguments.get("workspace_gid") or os.getenv("ASANA_WORKSPACE_GID", "")

    async with httpx.AsyncClient(
        base_url=ASANA_BASE, headers=_asana_headers(), timeout=30.0
    ) as client:
        if tool_name == "asana_list_tasks":
            params: dict[str, Any] = {
                "project": arguments["project_gid"],
                "opt_fields": arguments.get("opt_fields", "name,completed,due_on,assignee.name,notes"),
            }
            if arguments.get("completed_since"):
                params["completed_since"] = arguments["completed_since"]
            resp = await client.get("/tasks", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {"tasks": data.get("data", [])}

        elif tool_name == "asana_get_task":
            task_gid = arguments["task_gid"]
            params = {
                "opt_fields": arguments.get(
                    "opt_fields",
                    "name,notes,completed,due_on,assignee.name,projects.name,tags.name",
                )
            }
            resp = await client.get(f"/tasks/{task_gid}", params=params)
            resp.raise_for_status()
            return {"task": resp.json().get("data", {})}

        elif tool_name == "asana_create_task":
            task_data: dict[str, Any] = {"name": arguments["name"]}
            if arguments.get("notes"):
                task_data["notes"] = arguments["notes"]
            if arguments.get("due_on"):
                task_data["due_on"] = arguments["due_on"]
            if arguments.get("assignee"):
                task_data["assignee"] = arguments["assignee"]
            if arguments.get("parent"):
                task_data["parent"] = arguments["parent"]

            if arguments.get("project_gid"):
                task_data["projects"] = [arguments["project_gid"]]

            if not arguments.get("parent"):
                task_data["workspace"] = workspace_gid if workspace_gid else None

            payload = {"data": {k: v for k, v in task_data.items() if v is not None}}
            resp = await client.post("/tasks", json=payload)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {"task_gid": data.get("gid", ""), "name": data.get("name", "")}

        elif tool_name == "asana_update_task":
            task_gid = arguments["task_gid"]
            update: dict[str, Any] = {}
            for field in ["name", "notes", "completed", "due_on", "assignee"]:
                if field in arguments:
                    update[field] = arguments[field]
            resp = await client.put(f"/tasks/{task_gid}", json={"data": update})
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {"task_gid": data.get("gid", ""), "name": data.get("name", ""), "updated": True}

        elif tool_name == "asana_list_projects":
            if not workspace_gid:
                return {"error": "workspace_gid required (or set ASANA_WORKSPACE_GID env var)"}
            params = {
                "workspace": workspace_gid,
                "archived": str(arguments.get("archived", False)).lower(),
                "opt_fields": arguments.get("opt_fields", "name,archived,created_at,owner.name"),
            }
            resp = await client.get("/projects", params=params)
            resp.raise_for_status()
            return {"projects": resp.json().get("data", [])}

        elif tool_name == "asana_create_project":
            if not workspace_gid:
                return {"error": "workspace_gid required (or set ASANA_WORKSPACE_GID env var)"}
            project_data: dict[str, Any] = {
                "name": arguments["name"],
                "workspace": workspace_gid,
                "privacy_setting": arguments.get("privacy_setting", "public_to_workspace"),
            }
            if arguments.get("notes"):
                project_data["notes"] = arguments["notes"]
            if arguments.get("color"):
                project_data["color"] = arguments["color"]
            resp = await client.post("/projects", json={"data": project_data})
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {"project_gid": data.get("gid", ""), "name": data.get("name", "")}

        elif tool_name == "asana_add_comment":
            task_gid = arguments["task_gid"]
            payload = {"data": {"text": arguments["text"]}}
            resp = await client.post(f"/tasks/{task_gid}/stories", json=payload)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {"story_gid": data.get("gid", ""), "created_at": data.get("created_at", "")}

        else:
            return {"error": f"Unknown tool: {tool_name}"}
