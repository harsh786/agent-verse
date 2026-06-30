"""Teamwork MCP server — project management, tasks, and milestones.

Environment:
  TEAMWORK_API_KEY: Teamwork API key
  TEAMWORK_SITE:    Your Teamwork site subdomain (e.g. 'mycompany')
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    site = os.getenv("TEAMWORK_SITE", "")
    return f"https://{site}.teamwork.com/projects/api/v3"


TOOL_DEFINITIONS = [
    {
        "name": "teamwork_list_projects",
        "description": "List all projects in Teamwork",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "archived", "completed", "all"], "default": "active"},
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "teamwork_create_project",
        "description": "Create a new project in Teamwork",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "start_date": {"type": "string", "description": "YYYYMMDD"},
                "end_date": {"type": "string", "description": "YYYYMMDD"},
                "company_id": {"type": "integer"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "teamwork_list_tasks",
        "description": "List tasks for a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["active", "completed", "reopened", "all"]},
                "assignee_id": {"type": "integer"},
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 50},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "teamwork_create_task",
        "description": "Create a new task in a Teamwork project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "tasklist_id": {"type": "integer"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "due_date": {"type": "string", "description": "YYYYMMDD"},
                "assignee_ids": {"type": "array", "items": {"type": "integer"}},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
            },
            "required": ["project_id", "name"],
        },
    },
    {
        "name": "teamwork_complete_task",
        "description": "Mark a task as complete",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "teamwork_list_milestones",
        "description": "List milestones for a project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["active", "completed", "all"]},
            },
            "required": ["project_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("TEAMWORK_API_KEY", "")
    site = os.getenv("TEAMWORK_SITE", "")
    if not api_key or not site:
        return {"error": "TEAMWORK_API_KEY and TEAMWORK_SITE must be configured"}

    auth = (api_key, "x")
    base = _base()

    try:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as c:
            if tool_name == "teamwork_list_projects":
                params: dict[str, Any] = {
                    "status": arguments.get("status", "active"),
                    "page": arguments.get("page", 1),
                    "pageSize": arguments.get("page_size", 50),
                }
                r = await c.get(f"{base}/projects.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "teamwork_create_project":
                payload: dict[str, Any] = {"project": {"name": arguments["name"]}}
                for field in ("description", "start_date", "end_date", "company_id"):
                    mapped = {"start_date": "startDate", "end_date": "endDate", "company_id": "companyId"}.get(field, field)
                    if v := arguments.get(field):
                        payload["project"][mapped] = v
                r = await c.post(f"{base}/projects.json", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "teamwork_list_tasks":
                pid = arguments["project_id"]
                params = {
                    "page": arguments.get("page", 1),
                    "pageSize": arguments.get("page_size", 50),
                }
                if status := arguments.get("status"):
                    params["status"] = status
                if aid := arguments.get("assignee_id"):
                    params["assigneeUserId"] = aid
                r = await c.get(f"{base}/projects/{pid}/tasks.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "teamwork_create_task":
                pid = arguments["project_id"]
                task: dict[str, Any] = {"content": arguments["name"]}
                for field in ("description", "due_date", "assignee_ids", "priority"):
                    mapped = {"due_date": "dueDate", "assignee_ids": "assignedToUserIds"}.get(field, field)
                    if v := arguments.get(field):
                        task[mapped] = v
                if tlid := arguments.get("tasklist_id"):
                    task["taskListId"] = tlid
                r = await c.post(f"{base}/projects/{pid}/tasks.json", json={"todo-item": task})
                r.raise_for_status()
                return r.json()

            elif tool_name == "teamwork_complete_task":
                tid = arguments["task_id"]
                r = await c.put(f"{base}/tasks/{tid}/complete.json")
                r.raise_for_status()
                return r.json() if r.content else {"status": "completed"}

            elif tool_name == "teamwork_list_milestones":
                pid = arguments["project_id"]
                params = {}
                if status := arguments.get("status"):
                    params["status"] = status
                r = await c.get(f"{base}/projects/{pid}/milestones.json", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("teamwork_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
