"""Postman MCP server — API collections, environments, and test runs.

Environment:
  POSTMAN_API_KEY: Postman API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

POSTMAN_BASE = "https://api.getpostman.com"

TOOL_DEFINITIONS = [
    {
        "name": "postman_list_collections",
        "description": "List all Postman collections in the workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Workspace ID to filter by"},
            },
        },
    },
    {
        "name": "postman_get_collection",
        "description": "Get a Postman collection by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
            },
            "required": ["collection_id"],
        },
    },
    {
        "name": "postman_list_environments",
        "description": "List all Postman environments",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
            },
        },
    },
    {
        "name": "postman_get_environment",
        "description": "Get a Postman environment by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "environment_id": {"type": "string"},
            },
            "required": ["environment_id"],
        },
    },
    {
        "name": "postman_list_workspaces",
        "description": "List all Postman workspaces",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["personal", "team", "private", "public", "partner"],
                },
            },
        },
    },
    {
        "name": "postman_list_apis",
        "description": "List APIs defined in Postman",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "since": {"type": "string", "description": "ISO 8601 datetime filter"},
                "until": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "postman_run_monitor",
        "description": "Trigger a run of a Postman monitor",
        "parameters": {
            "type": "object",
            "properties": {
                "monitor_id": {"type": "string"},
            },
            "required": ["monitor_id"],
        },
    },
    {
        "name": "postman_list_monitors",
        "description": "List all Postman monitors",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
            },
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("POSTMAN_API_KEY", "")
    return {
        "X-Api-Key": key,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("POSTMAN_API_KEY", "")
    if not key:
        return {"error": "POSTMAN_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(base_url=POSTMAN_BASE, headers=_headers(), timeout=30.0) as c:
            if tool_name == "postman_list_collections":
                params: dict[str, Any] = {}
                if ws := arguments.get("workspace"):
                    params["workspace"] = ws
                r = await c.get("/collections", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "postman_get_collection":
                r = await c.get(f"/collections/{arguments['collection_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "postman_list_environments":
                params = {}
                if ws := arguments.get("workspace"):
                    params["workspace"] = ws
                r = await c.get("/environments", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "postman_get_environment":
                r = await c.get(f"/environments/{arguments['environment_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "postman_list_workspaces":
                params = {}
                if ws_type := arguments.get("type"):
                    params["type"] = ws_type
                r = await c.get("/workspaces", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "postman_list_apis":
                params = {
                    "limit": arguments.get("limit", 10),
                    "offset": arguments.get("offset", 0),
                }
                if ws := arguments.get("workspace"):
                    params["workspace"] = ws
                if since := arguments.get("since"):
                    params["since"] = since
                if until := arguments.get("until"):
                    params["until"] = until
                r = await c.get("/apis", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "postman_run_monitor":
                r = await c.post(f"/monitors/{arguments['monitor_id']}/run")
                r.raise_for_status()
                return r.json()

            elif tool_name == "postman_list_monitors":
                params = {}
                if ws := arguments.get("workspace"):
                    params["workspace"] = ws
                r = await c.get("/monitors", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("postman_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
