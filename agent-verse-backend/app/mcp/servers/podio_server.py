"""Podio MCP server — project management: apps, items, spaces, and tasks.

Environment variables:
  PODIO_ACCESS_TOKEN: Podio OAuth 2.0 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

PODIO_BASE = "https://api.podio.com"

TOOL_DEFINITIONS = [
    {
        "name": "podio_list_apps",
        "description": "List Podio apps (databases/boards) in a space or organisation",
        "parameters": {
            "type": "object",
            "properties": {
                "space_id": {"type": "integer", "description": "Space ID to list apps for"},
                "org_id": {"type": "integer", "description": "Organisation ID (used if no space_id)"},
            },
        },
    },
    {
        "name": "podio_list_items",
        "description": "List items in a Podio app with optional filtering and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "app_id": {"type": "integer", "description": "Podio app ID"},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
                "sort_by": {"type": "string", "description": "Sort field name"},
                "sort_desc": {"type": "boolean", "default": True},
                "filters": {"type": "object", "description": "Field filters as key-value pairs"},
            },
            "required": ["app_id"],
        },
    },
    {
        "name": "podio_create_item",
        "description": "Create a new item in a Podio app",
        "parameters": {
            "type": "object",
            "properties": {
                "app_id": {"type": "integer", "description": "Podio app ID"},
                "fields": {
                    "type": "object",
                    "description": "Item field values as {field_id_or_external_id: value}",
                },
                "external_id": {"type": "string", "description": "Your external reference ID"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["app_id", "fields"],
        },
    },
    {
        "name": "podio_update_item",
        "description": "Update an existing item in Podio by item ID",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer", "description": "Podio item ID"},
                "fields": {
                    "type": "object",
                    "description": "Field values to update as {field_id_or_external_id: value}",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["item_id", "fields"],
        },
    },
    {
        "name": "podio_list_spaces",
        "description": "List spaces (workspaces) in a Podio organisation",
        "parameters": {
            "type": "object",
            "properties": {
                "org_id": {"type": "integer", "description": "Organisation ID"},
            },
            "required": ["org_id"],
        },
    },
    {
        "name": "podio_create_task",
        "description": "Create a task in Podio, optionally linked to an item, app, or space",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Task description/title"},
                "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)"},
                "responsible": {"type": "integer", "description": "Podio user ID to assign the task to"},
                "ref_type": {
                    "type": "string",
                    "enum": ["item", "app", "space"],
                    "description": "Reference object type",
                },
                "ref_id": {"type": "integer", "description": "Reference object ID"},
                "private": {"type": "boolean", "default": False},
            },
            "required": ["text"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"OAuth2 {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("PODIO_ACCESS_TOKEN", "")
    if not token:
        return {"error": "PODIO_ACCESS_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=PODIO_BASE, headers=_headers(token), timeout=30.0
        ) as c:
            if tool_name == "podio_list_apps":
                if "space_id" in arguments:
                    r = await c.get(f"/app/space/{arguments['space_id']}/")
                elif "org_id" in arguments:
                    r = await c.get(f"/app/org/{arguments['org_id']}/")
                else:
                    r = await c.get("/app/")
                r.raise_for_status()
                return r.json()

            elif tool_name == "podio_list_items":
                app_id = arguments["app_id"]
                body: dict[str, Any] = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                    "sort_desc": arguments.get("sort_desc", True),
                }
                if "sort_by" in arguments:
                    body["sort_by"] = arguments["sort_by"]
                if "filters" in arguments:
                    body["filters"] = arguments["filters"]
                r = await c.post(f"/item/app/{app_id}/filter/", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "podio_create_item":
                app_id = arguments["app_id"]
                body = {"fields": arguments["fields"]}
                if "external_id" in arguments:
                    body["external_id"] = arguments["external_id"]
                if "tags" in arguments:
                    body["tags"] = arguments["tags"]
                r = await c.post(f"/item/app/{app_id}/", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "podio_update_item":
                item_id = arguments["item_id"]
                body = {"fields": arguments["fields"]}
                if "tags" in arguments:
                    body["tags"] = arguments["tags"]
                r = await c.put(f"/item/{item_id}", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "podio_list_spaces":
                org_id = arguments["org_id"]
                r = await c.get(f"/space/org/{org_id}/")
                r.raise_for_status()
                return r.json()

            elif tool_name == "podio_create_task":
                body = {"text": arguments["text"]}
                if "due_date" in arguments:
                    body["due_date"] = arguments["due_date"]
                if "responsible" in arguments:
                    body["responsible"] = {"user_id": arguments["responsible"]}
                if "ref_type" in arguments and "ref_id" in arguments:
                    body["ref"] = {"type": arguments["ref_type"], "id": arguments["ref_id"]}
                if "private" in arguments:
                    body["private"] = arguments["private"]
                r = await c.post("/task/", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("podio_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
