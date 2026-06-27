"""Todoist MCP server — Todoist REST API v2 integration.

Environment variables:
  TODOIST_API_TOKEN: Todoist personal API token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TODOIST_BASE = "https://api.todoist.com/rest/v2"

TOOL_DEFINITIONS = [
    {
        "name": "todoist_list_tasks",
        "description": "List active Todoist tasks with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Filter by project ID"},
                "section_id": {"type": "string", "description": "Filter by section ID"},
                "label": {"type": "string", "description": "Filter by label name"},
                "filter": {
                    "type": "string",
                    "description": "Natural language filter, e.g. 'today', 'overdue', 'priority 1'",
                },
                "lang": {"type": "string", "default": "en"},
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific task IDs to retrieve",
                },
            },
        },
    },
    {
        "name": "todoist_get_task",
        "description": "Get a single Todoist task by its ID",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "todoist_create_task",
        "description": "Create a new Todoist task",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Task content/title"},
                "description": {"type": "string", "default": ""},
                "project_id": {"type": "string"},
                "section_id": {"type": "string"},
                "parent_id": {"type": "string", "description": "Parent task ID for subtasks"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "priority": {
                    "type": "integer",
                    "enum": [1, 2, 3, 4],
                    "description": "1=Normal, 2=Medium, 3=High, 4=Very High",
                    "default": 1,
                },
                "due_string": {
                    "type": "string",
                    "description": "Natural language due date, e.g. 'tomorrow', 'next Monday'",
                },
                "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                "due_datetime": {"type": "string", "description": "Due datetime in ISO 8601"},
                "assignee_id": {"type": "string"},
                "duration": {"type": "integer"},
                "duration_unit": {
                    "type": "string",
                    "enum": ["minute", "day"],
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "todoist_update_task",
        "description": "Update an existing Todoist task",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "content": {"type": "string"},
                "description": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "priority": {"type": "integer", "enum": [1, 2, 3, 4]},
                "due_string": {"type": "string"},
                "due_date": {"type": "string"},
                "due_datetime": {"type": "string"},
                "assignee_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "todoist_close_task",
        "description": "Mark a Todoist task as completed",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "todoist_list_projects",
        "description": "List all Todoist projects",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "todoist_create_project",
        "description": "Create a new Todoist project",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "parent_id": {"type": "string", "description": "Parent project ID"},
                "color": {
                    "type": "string",
                    "description": "Project color name, e.g. 'red', 'blue', 'green'",
                },
                "is_favorite": {"type": "boolean", "default": False},
                "view_style": {
                    "type": "string",
                    "enum": ["list", "board"],
                    "default": "list",
                },
            },
            "required": ["name"],
        },
    },
]


def _todoist_headers() -> dict[str, str]:
    token = os.getenv("TODOIST_API_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("TODOIST_API_TOKEN", "")
    if not token:
        return {"error": "TODOIST_API_TOKEN not configured"}

    async with httpx.AsyncClient(
        base_url=TODOIST_BASE, headers=_todoist_headers(), timeout=30.0
    ) as client:
        if tool_name == "todoist_list_tasks":
            params: dict[str, Any] = {}
            for key in ["project_id", "section_id", "label", "filter", "lang"]:
                if arguments.get(key):
                    params[key] = arguments[key]
            if arguments.get("ids"):
                params["ids"] = ",".join(arguments["ids"])

            resp = await client.get("/tasks", params=params)
            resp.raise_for_status()
            return {"tasks": resp.json()}

        elif tool_name == "todoist_get_task":
            resp = await client.get(f"/tasks/{arguments['task_id']}")
            resp.raise_for_status()
            return {"task": resp.json()}

        elif tool_name == "todoist_create_task":
            payload: dict[str, Any] = {"content": arguments["content"]}
            optional_fields = [
                "description", "project_id", "section_id", "parent_id",
                "labels", "priority", "due_string", "due_date", "due_datetime",
                "assignee_id", "duration", "duration_unit",
            ]
            for field in optional_fields:
                if field in arguments and arguments[field] is not None:
                    payload[field] = arguments[field]

            resp = await client.post("/tasks", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "task_id": data.get("id", ""),
                "content": data.get("content", ""),
                "url": data.get("url", ""),
            }

        elif tool_name == "todoist_update_task":
            task_id = arguments["task_id"]
            payload: dict[str, Any] = {}
            updatable = [
                "content", "description", "labels", "priority",
                "due_string", "due_date", "due_datetime", "assignee_id",
            ]
            for field in updatable:
                if field in arguments:
                    payload[field] = arguments[field]

            resp = await client.post(f"/tasks/{task_id}", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"task_id": data.get("id", ""), "content": data.get("content", ""), "updated": True}

        elif tool_name == "todoist_close_task":
            resp = await client.post(f"/tasks/{arguments['task_id']}/close")
            resp.raise_for_status()
            return {"task_id": arguments["task_id"], "completed": True}

        elif tool_name == "todoist_list_projects":
            resp = await client.get("/projects")
            resp.raise_for_status()
            return {"projects": resp.json()}

        elif tool_name == "todoist_create_project":
            payload = {"name": arguments["name"]}
            for field in ["parent_id", "color", "is_favorite", "view_style"]:
                if field in arguments and arguments[field] is not None:
                    payload[field] = arguments[field]

            resp = await client.post("/projects", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"project_id": data.get("id", ""), "name": data.get("name", ""), "url": data.get("url", "")}

        else:
            return {"error": f"Unknown tool: {tool_name}"}
