"""Basecamp MCP server — Basecamp 3 REST API integration.

Environment variables:
  BASECAMP_ACCOUNT_ID: Basecamp account ID (numeric)
  BASECAMP_ACCESS_TOKEN: OAuth 2.0 access token or personal access token
  BASECAMP_USER_AGENT: App identifier required by Basecamp API policy
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASECAMP_API_VERSION = "v1"

TOOL_DEFINITIONS = [
    {
        "name": "basecamp_list_projects",
        "description": "List all active Basecamp projects for the account",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "archived", "trashed"],
                    "default": "active",
                },
            },
        },
    },
    {
        "name": "basecamp_get_project",
        "description": "Get details of a specific Basecamp project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "basecamp_list_todolists",
        "description": "List all to-do lists in a Basecamp project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "basecamp_list_todos",
        "description": "List to-dos in a Basecamp to-do list",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "todolist_id": {"type": "integer"},
                "completed": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include completed todos",
                },
            },
            "required": ["project_id", "todolist_id"],
        },
    },
    {
        "name": "basecamp_create_todo",
        "description": "Create a new to-do item in a Basecamp to-do list",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "todolist_id": {"type": "integer"},
                "content": {"type": "string", "description": "To-do item content"},
                "description": {"type": "string", "default": ""},
                "assignee_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "User IDs to assign",
                },
                "due_on": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                "notify": {
                    "type": "boolean",
                    "default": False,
                    "description": "Notify assignees",
                },
            },
            "required": ["project_id", "todolist_id", "content"],
        },
    },
    {
        "name": "basecamp_complete_todo",
        "description": "Mark a Basecamp to-do as completed",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "todo_id": {"type": "integer"},
            },
            "required": ["project_id", "todo_id"],
        },
    },
    {
        "name": "basecamp_create_message",
        "description": "Create a message (announcement) in a Basecamp project",
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "message_board_id": {"type": "integer"},
                "subject": {"type": "string"},
                "content": {"type": "string", "description": "Message body (HTML allowed)"},
                "status": {
                    "type": "string",
                    "enum": ["active", "draft"],
                    "default": "active",
                },
            },
            "required": ["project_id", "message_board_id", "subject"],
        },
    },
    {
        "name": "basecamp_list_people",
        "description": "List people in the Basecamp account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


def _basecamp_headers() -> dict[str, str]:
    token = os.getenv("BASECAMP_ACCESS_TOKEN", "")
    user_agent = os.getenv("BASECAMP_USER_AGENT", "AgentVerse/1.0 (support@example.com)")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return await _call_tool_inner(tool_name, arguments)
    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.text[:500]
        except Exception:
            pass
        return {"error": f"HTTP {exc.response.status_code}: {error_body or exc.response.reason_phrase}", "status_code": exc.response.status_code}
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}


async def _call_tool_inner(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = os.getenv("BASECAMP_ACCOUNT_ID", "")
    token = os.getenv("BASECAMP_ACCESS_TOKEN", "")
    if not account_id or not token:
        return {"error": "BASECAMP_ACCOUNT_ID and BASECAMP_ACCESS_TOKEN must be configured"}

    base_url = f"https://3.basecampapi.com/{account_id}"

    async with httpx.AsyncClient(
        base_url=base_url, headers=_basecamp_headers(), timeout=30.0
    ) as client:
        if tool_name == "basecamp_list_projects":
            params: dict[str, Any] = {}
            if arguments.get("status", "active") != "active":
                params["status"] = arguments["status"]
            resp = await client.get("/projects.json", params=params)
            resp.raise_for_status()
            projects = resp.json()
            return {
                "projects": [
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "description": p.get("description", ""),
                        "status": p.get("status", ""),
                        "created_at": p.get("created_at", ""),
                        "updated_at": p.get("updated_at", ""),
                    }
                    for p in projects
                ]
            }

        elif tool_name == "basecamp_get_project":
            resp = await client.get(f"/projects/{arguments['project_id']}.json")
            resp.raise_for_status()
            p = resp.json()
            return {
                "id": p["id"],
                "name": p["name"],
                "description": p.get("description", ""),
                "status": p.get("status", ""),
                "dock": [
                    {"type": t["name"], "id": t["id"], "url": t.get("url", "")}
                    for t in p.get("dock", [])
                ],
            }

        elif tool_name == "basecamp_list_todolists":
            project_id = arguments["project_id"]
            resp = await client.get(f"/buckets/{project_id}/todolists.json")
            resp.raise_for_status()
            return {"todolists": resp.json()}

        elif tool_name == "basecamp_list_todos":
            project_id = arguments["project_id"]
            todolist_id = arguments["todolist_id"]
            params = {}
            if arguments.get("completed"):
                params["completed"] = "true"
            resp = await client.get(
                f"/buckets/{project_id}/todolists/{todolist_id}/todos.json",
                params=params,
            )
            resp.raise_for_status()
            todos = resp.json()
            return {
                "todos": [
                    {
                        "id": t["id"],
                        "content": t["content"],
                        "completed": t.get("completed", False),
                        "due_on": t.get("due_on"),
                        "assignees": [a.get("name", "") for a in t.get("assignees", [])],
                    }
                    for t in todos
                ]
            }

        elif tool_name == "basecamp_create_todo":
            project_id = arguments["project_id"]
            todolist_id = arguments["todolist_id"]
            payload: dict[str, Any] = {
                "content": arguments["content"],
                "notify": arguments.get("notify", False),
            }
            if arguments.get("description"):
                payload["description"] = arguments["description"]
            if arguments.get("due_on"):
                payload["due_on"] = arguments["due_on"]
            if arguments.get("assignee_ids"):
                payload["assignee_ids"] = arguments["assignee_ids"]

            resp = await client.post(
                f"/buckets/{project_id}/todolists/{todolist_id}/todos.json",
                json=payload,
            )
            resp.raise_for_status()
            t = resp.json()
            return {"todo_id": t["id"], "content": t["content"], "created": True}

        elif tool_name == "basecamp_complete_todo":
            project_id = arguments["project_id"]
            todo_id = arguments["todo_id"]
            resp = await client.post(
                f"/buckets/{project_id}/todos/{todo_id}/completion.json"
            )
            resp.raise_for_status()
            return {"todo_id": todo_id, "completed": True}

        elif tool_name == "basecamp_create_message":
            project_id = arguments["project_id"]
            board_id = arguments["message_board_id"]
            payload = {
                "subject": arguments["subject"],
                "content": arguments.get("content", ""),
                "status": arguments.get("status", "active"),
            }
            resp = await client.post(
                f"/buckets/{project_id}/message_boards/{board_id}/messages.json",
                json=payload,
            )
            resp.raise_for_status()
            m = resp.json()
            return {
                "message_id": m["id"],
                "subject": m["subject"],
                "app_url": m.get("app_url", ""),
            }

        elif tool_name == "basecamp_list_people":
            resp = await client.get("/people.json")
            resp.raise_for_status()
            people = resp.json()
            return {
                "people": [
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "email_address": p.get("email_address", ""),
                        "admin": p.get("admin", False),
                    }
                    for p in people
                ]
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
