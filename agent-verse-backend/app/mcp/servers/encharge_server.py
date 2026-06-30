"""Encharge MCP server — marketing automation: users, tags, events, and segments.

Environment variables:
  ENCHARGE_API_KEY: Encharge API key from account settings
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

ENCHARGE_BASE = "https://api.encharge.io/v1"

TOOL_DEFINITIONS = [
    {
        "name": "encharge_create_user",
        "description": "Create or upsert a user in Encharge with email, name, and custom attributes",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "User's email address (required identifier)"},
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "userId": {"type": "string", "description": "Your internal user ID"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to apply to the user",
                },
                "fields": {"type": "object", "description": "Additional custom field key-value pairs"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "encharge_update_user",
        "description": "Update an existing Encharge user's attributes by email",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "User's email address (identifier)"},
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "phone": {"type": "string"},
                "fields": {"type": "object", "description": "Custom field key-value pairs to update"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "encharge_add_tag",
        "description": "Add one or more tags to an Encharge user",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "User's email address"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to add",
                },
            },
            "required": ["email", "tags"],
        },
    },
    {
        "name": "encharge_remove_tag",
        "description": "Remove one or more tags from an Encharge user",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "User's email address"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to remove",
                },
            },
            "required": ["email", "tags"],
        },
    },
    {
        "name": "encharge_track_event",
        "description": "Track a custom event for an Encharge user to trigger automations",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "User's email address"},
                "name": {"type": "string", "description": "Event name, e.g. 'Signed Up' or 'Upgraded Plan'"},
                "properties": {"type": "object", "description": "Event properties as key-value pairs"},
            },
            "required": ["email", "name"],
        },
    },
    {
        "name": "encharge_list_segments",
        "description": "List all audience segments defined in Encharge",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 50},
            },
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "X-Encharge-Token": api_key,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("ENCHARGE_API_KEY", "")
    if not api_key:
        return {"error": "ENCHARGE_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=ENCHARGE_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "encharge_create_user":
                body: dict[str, Any] = {"email": arguments["email"]}
                for k in ("firstName", "lastName", "userId", "tags"):
                    if k in arguments:
                        body[k] = arguments[k]
                if "fields" in arguments:
                    body.update(arguments["fields"])
                r = await c.post("/people", json={"person": body})
                r.raise_for_status()
                return r.json()

            elif tool_name == "encharge_update_user":
                body = {"email": arguments["email"]}
                for k in ("firstName", "lastName", "phone"):
                    if k in arguments:
                        body[k] = arguments[k]
                if "fields" in arguments:
                    body.update(arguments["fields"])
                r = await c.patch("/people", json={"person": body})
                r.raise_for_status()
                return r.json()

            elif tool_name == "encharge_add_tag":
                body = {
                    "person": {"email": arguments["email"]},
                    "tag": arguments["tags"] if isinstance(arguments["tags"], str)
                    else ",".join(arguments["tags"]),
                }
                r = await c.post("/tags", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "encharge_remove_tag":
                body = {
                    "person": {"email": arguments["email"]},
                    "tag": arguments["tags"] if isinstance(arguments["tags"], str)
                    else ",".join(arguments["tags"]),
                }
                r = await c.delete("/tags", json=body)
                r.raise_for_status()
                return {"removed": True}

            elif tool_name == "encharge_track_event":
                body = {
                    "name": arguments["name"],
                    "user": {"email": arguments["email"]},
                }
                if "properties" in arguments:
                    body["properties"] = arguments["properties"]
                r = await c.post("/events", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "encharge_list_segments":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 50),
                }
                r = await c.get("/segments", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("encharge_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
