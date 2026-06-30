"""Knack MCP server — no-code database records, objects, and views.

Environment:
  KNACK_APP_ID:  Knack application ID
  KNACK_API_KEY: Knack API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.knack.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "knack_get_records",
        "description": "Get records from a Knack object",
        "parameters": {
            "type": "object",
            "properties": {
                "object_key": {"type": "string", "description": "Object key (e.g. object_1)"},
                "page": {"type": "integer", "default": 1},
                "rows_per_page": {"type": "integer", "default": 25},
                "sort_field": {"type": "string"},
                "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
            },
            "required": ["object_key"],
        },
    },
    {
        "name": "knack_create_record",
        "description": "Create a new record in a Knack object",
        "parameters": {
            "type": "object",
            "properties": {
                "object_key": {"type": "string"},
                "fields": {"type": "object", "description": "Field values keyed by field ID"},
            },
            "required": ["object_key", "fields"],
        },
    },
    {
        "name": "knack_update_record",
        "description": "Update an existing record in a Knack object",
        "parameters": {
            "type": "object",
            "properties": {
                "object_key": {"type": "string"},
                "record_id": {"type": "string"},
                "fields": {"type": "object"},
            },
            "required": ["object_key", "record_id", "fields"],
        },
    },
    {
        "name": "knack_delete_record",
        "description": "Delete a record from a Knack object",
        "parameters": {
            "type": "object",
            "properties": {
                "object_key": {"type": "string"},
                "record_id": {"type": "string"},
            },
            "required": ["object_key", "record_id"],
        },
    },
    {
        "name": "knack_list_objects",
        "description": "List all objects (tables) in the Knack application schema",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "knack_run_view",
        "description": "Run a specific Knack view to retrieve filtered/sorted data",
        "parameters": {
            "type": "object",
            "properties": {
                "scene_key": {"type": "string", "description": "Scene key (e.g. scene_1)"},
                "view_key": {"type": "string", "description": "View key (e.g. view_1)"},
                "page": {"type": "integer", "default": 1},
                "rows_per_page": {"type": "integer", "default": 25},
            },
            "required": ["scene_key", "view_key"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    app_id = os.getenv("KNACK_APP_ID", "")
    api_key = os.getenv("KNACK_API_KEY", "")
    if not app_id or not api_key:
        return {"error": "KNACK_APP_ID and KNACK_API_KEY must be configured"}

    headers = {
        "X-Knack-Application-Id": app_id,
        "X-Knack-REST-API-Key": api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "knack_get_records":
                obj = arguments["object_key"]
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "rows_per_page": arguments.get("rows_per_page", 25),
                }
                if sf := arguments.get("sort_field"):
                    params["sort_field"] = sf
                    params["sort_order"] = arguments.get("sort_order", "asc")
                r = await c.get(f"{BASE}/objects/{obj}/records", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "knack_create_record":
                obj = arguments["object_key"]
                r = await c.post(f"{BASE}/objects/{obj}/records", json=arguments["fields"])
                r.raise_for_status()
                return r.json()

            elif tool_name == "knack_update_record":
                obj = arguments["object_key"]
                rid = arguments["record_id"]
                r = await c.put(f"{BASE}/objects/{obj}/records/{rid}", json=arguments["fields"])
                r.raise_for_status()
                return r.json()

            elif tool_name == "knack_delete_record":
                obj = arguments["object_key"]
                rid = arguments["record_id"]
                r = await c.delete(f"{BASE}/objects/{obj}/records/{rid}")
                r.raise_for_status()
                return r.json() if r.content else {"status": "deleted"}

            elif tool_name == "knack_list_objects":
                r = await c.get(f"{BASE}/objects")
                r.raise_for_status()
                return r.json()

            elif tool_name == "knack_run_view":
                scene = arguments["scene_key"]
                view = arguments["view_key"]
                params = {
                    "page": arguments.get("page", 1),
                    "rows_per_page": arguments.get("rows_per_page", 25),
                }
                r = await c.get(f"{BASE}/pages/{scene}/views/{view}/records", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("knack_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
