"""Wrike MCP server — Wrike REST API v4 integration.

Environment variables:
  WRIKE_ACCESS_TOKEN: Wrike permanent or OAuth access token
  WRIKE_HOST: Override host for Wrike GovCloud (default: www.wrike.com)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "wrike_list_folders",
        "description": "List Wrike folders and projects in a space or parent folder",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_id": {
                    "type": "string",
                    "description": "Parent folder ID. Omit to list root folders.",
                },
                "project": {
                    "type": "boolean",
                    "description": "True to list only projects",
                },
            },
        },
    },
    {
        "name": "wrike_list_tasks",
        "description": "List tasks in a Wrike folder or project",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["Active", "Completed", "Deferred", "Cancelled"],
                    "description": "Filter by task status",
                },
                "assignee": {
                    "type": "string",
                    "description": "Filter by assignee contact ID",
                },
                "page_size": {"type": "integer", "default": 100},
            },
            "required": ["folder_id"],
        },
    },
    {
        "name": "wrike_get_task",
        "description": "Get a single Wrike task by its ID",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "wrike_create_task",
        "description": "Create a new task in a Wrike folder or project",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "status": {
                    "type": "string",
                    "enum": ["Active", "Completed", "Deferred", "Cancelled"],
                    "default": "Active",
                },
                "importance": {
                    "type": "string",
                    "enum": ["High", "Normal", "Low"],
                    "default": "Normal",
                },
                "dates": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["Backlog", "Milestone", "Planned"]},
                        "start": {"type": "string"},
                        "due": {"type": "string"},
                    },
                },
                "responsible_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Contact IDs to assign as responsible",
                },
            },
            "required": ["folder_id", "title"],
        },
    },
    {
        "name": "wrike_update_task",
        "description": "Update an existing Wrike task",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["Active", "Completed", "Deferred", "Cancelled"],
                },
                "importance": {
                    "type": "string",
                    "enum": ["High", "Normal", "Low"],
                },
                "dates": {"type": "object"},
                "responsible_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "wrike_add_comment",
        "description": "Add a comment to a Wrike task",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["task_id", "text"],
        },
    },
    {
        "name": "wrike_list_contacts",
        "description": "List Wrike account contacts (users)",
        "parameters": {
            "type": "object",
            "properties": {
                "me": {
                    "type": "boolean",
                    "description": "Return only the current user's contact",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "wrike_list_spaces",
        "description": "List Wrike spaces in the account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


def _wrike_headers() -> dict[str, str]:
    token = os.getenv("WRIKE_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
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
    token = os.getenv("WRIKE_ACCESS_TOKEN", "")
    if not token:
        return {"error": "WRIKE_ACCESS_TOKEN not configured"}

    host = os.getenv("WRIKE_HOST", "www.wrike.com")
    base_url = f"https://{host}/api/v4"

    async with httpx.AsyncClient(
        base_url=base_url, headers=_wrike_headers(), timeout=30.0
    ) as client:
        if tool_name == "wrike_list_folders":
            if arguments.get("folder_id"):
                resp = await client.get(f"/folders/{arguments['folder_id']}/folders")
            else:
                resp = await client.get("/folders")
            resp.raise_for_status()
            data = resp.json()
            folders = data.get("data", [])
            if arguments.get("project"):
                folders = [f for f in folders if f.get("project")]
            return {
                "folders": [
                    {
                        "id": f["id"],
                        "title": f["title"],
                        "child_ids": f.get("childIds", []),
                        "project": bool(f.get("project")),
                    }
                    for f in folders
                ]
            }

        elif tool_name == "wrike_list_tasks":
            folder_id = arguments["folder_id"]
            params: dict[str, Any] = {}
            if arguments.get("status"):
                params["status"] = arguments["status"]
            if arguments.get("assignee"):
                params["responsibles"] = f"[{arguments['assignee']}]"
            if arguments.get("page_size"):
                params["pageSize"] = arguments["page_size"]

            resp = await client.get(f"/folders/{folder_id}/tasks", params=params)
            resp.raise_for_status()
            return {"tasks": resp.json().get("data", [])}

        elif tool_name == "wrike_get_task":
            resp = await client.get(f"/tasks/{arguments['task_id']}")
            resp.raise_for_status()
            tasks = resp.json().get("data", [])
            return {"task": tasks[0] if tasks else {}}

        elif tool_name == "wrike_create_task":
            folder_id = arguments["folder_id"]
            payload: dict[str, Any] = {
                "title": arguments["title"],
                "status": arguments.get("status", "Active"),
                "importance": arguments.get("importance", "Normal"),
            }
            if arguments.get("description"):
                payload["description"] = arguments["description"]
            if arguments.get("dates"):
                payload["dates"] = arguments["dates"]
            if arguments.get("responsible_ids"):
                payload["responsibles"] = arguments["responsible_ids"]

            resp = await client.post(f"/folders/{folder_id}/tasks", json=payload)
            resp.raise_for_status()
            tasks = resp.json().get("data", [])
            task = tasks[0] if tasks else {}
            return {"task_id": task.get("id", ""), "title": task.get("title", ""), "status": task.get("status", "")}

        elif tool_name == "wrike_update_task":
            task_id = arguments["task_id"]
            payload: dict[str, Any] = {}
            for field in ["title", "description", "status", "importance", "dates"]:
                if field in arguments:
                    payload[field] = arguments[field]
            if "responsible_ids" in arguments:
                payload["responsibles"] = arguments["responsible_ids"]

            resp = await client.put(f"/tasks/{task_id}", json=payload)
            resp.raise_for_status()
            tasks = resp.json().get("data", [])
            task = tasks[0] if tasks else {}
            return {"task_id": task.get("id", ""), "title": task.get("title", ""), "updated": True}

        elif tool_name == "wrike_add_comment":
            task_id = arguments["task_id"]
            payload = {"text": arguments["text"]}
            resp = await client.post(f"/tasks/{task_id}/comments", json=payload)
            resp.raise_for_status()
            comments = resp.json().get("data", [])
            comment = comments[0] if comments else {}
            return {"comment_id": comment.get("id", ""), "created": True}

        elif tool_name == "wrike_list_contacts":
            params: dict[str, Any] = {}
            if arguments.get("me"):
                params["me"] = "true"
            resp = await client.get("/contacts", params=params)
            resp.raise_for_status()
            contacts = resp.json().get("data", [])
            return {
                "contacts": [
                    {
                        "id": c["id"],
                        "first_name": c.get("firstName", ""),
                        "last_name": c.get("lastName", ""),
                        "type": c.get("type", ""),
                        "profiles": c.get("profiles", []),
                    }
                    for c in contacts
                ]
            }

        elif tool_name == "wrike_list_spaces":
            resp = await client.get("/spaces")
            resp.raise_for_status()
            return {"spaces": resp.json().get("data", [])}

        else:
            return {"error": f"Unknown tool: {tool_name}"}
