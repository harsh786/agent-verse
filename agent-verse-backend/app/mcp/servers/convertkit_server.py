"""ConvertKit MCP server — subscribers, forms, sequences, and tags.

Environment:
  CONVERTKIT_API_KEY: ConvertKit API key (v3)
  CONVERTKIT_API_SECRET: ConvertKit API secret (for admin operations)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

CONVERTKIT_BASE = "https://api.convertkit.com/v3"

TOOL_DEFINITIONS = [
    {
        "name": "convertkit_list_subscribers",
        "description": "List ConvertKit subscribers",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "sort_order": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "default": "asc",
                },
                "email_address": {"type": "string", "description": "Filter by exact email"},
            },
        },
    },
    {
        "name": "convertkit_create_subscriber",
        "description": "Subscribe an email to a ConvertKit form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string", "description": "ConvertKit form ID"},
                "email": {"type": "string"},
                "first_name": {"type": "string"},
                "fields": {"type": "object", "description": "Custom field values"},
                "tags": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Tag IDs to apply",
                },
            },
            "required": ["form_id", "email"],
        },
    },
    {
        "name": "convertkit_list_forms",
        "description": "List all ConvertKit landing pages and forms",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "convertkit_list_tags",
        "description": "List all ConvertKit tags",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "convertkit_tag_subscriber",
        "description": "Add a tag to a subscriber by email",
        "parameters": {
            "type": "object",
            "properties": {
                "tag_id": {"type": "string"},
                "email": {"type": "string"},
                "first_name": {"type": "string"},
            },
            "required": ["tag_id", "email"],
        },
    },
    {
        "name": "convertkit_list_sequences",
        "description": "List ConvertKit email sequences",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "convertkit_unsubscribe",
        "description": "Unsubscribe an email from all ConvertKit sequences",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
            },
            "required": ["email"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("CONVERTKIT_API_KEY", "")
    api_secret = os.getenv("CONVERTKIT_API_SECRET", "")
    if not api_key:
        return {"error": "CONVERTKIT_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=CONVERTKIT_BASE, timeout=30.0
        ) as c:
            if tool_name == "convertkit_list_subscribers":
                params: dict[str, Any] = {
                    "api_secret": api_secret or api_key,
                    "page": arguments.get("page", 1),
                    "sort_order": arguments.get("sort_order", "asc"),
                }
                if "email_address" in arguments:
                    params["email_address"] = arguments["email_address"]
                r = await c.get("/subscribers", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "subscribers": [
                        {"id": s.get("id"), "email": s.get("email_address"), "state": s.get("state")}
                        for s in data.get("subscribers", [])
                    ],
                    "total_subscribers": data.get("total_subscribers", 0),
                }

            elif tool_name == "convertkit_create_subscriber":
                payload: dict[str, Any] = {
                    "api_key": api_key,
                    "email": arguments["email"],
                }
                if "first_name" in arguments:
                    payload["first_name"] = arguments["first_name"]
                if "fields" in arguments:
                    payload["fields"] = arguments["fields"]
                if "tags" in arguments:
                    payload["tags"] = arguments["tags"]
                r = await c.post(f"/forms/{arguments['form_id']}/subscribe", json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("subscription", {}).get("id"),
                    "state": data.get("subscription", {}).get("state"),
                }

            elif tool_name == "convertkit_list_forms":
                r = await c.get("/forms", params={"api_key": api_key})
                r.raise_for_status()
                data = r.json()
                return {
                    "forms": [
                        {"id": f.get("id"), "name": f.get("name"), "type": f.get("type")}
                        for f in data.get("forms", [])
                    ]
                }

            elif tool_name == "convertkit_list_tags":
                r = await c.get("/tags", params={"api_key": api_key})
                r.raise_for_status()
                data = r.json()
                return {
                    "tags": [
                        {"id": t.get("id"), "name": t.get("name")}
                        for t in data.get("tags", [])
                    ]
                }

            elif tool_name == "convertkit_tag_subscriber":
                payload = {
                    "api_key": api_key,
                    "email": arguments["email"],
                }
                if "first_name" in arguments:
                    payload["first_name"] = arguments["first_name"]
                r = await c.post(
                    f"/tags/{arguments['tag_id']}/subscribe", json=payload
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("subscription", {}).get("id"),
                    "state": data.get("subscription", {}).get("state"),
                }

            elif tool_name == "convertkit_list_sequences":
                r = await c.get("/sequences", params={"api_key": api_key})
                r.raise_for_status()
                data = r.json()
                return {
                    "sequences": [
                        {"id": s.get("id"), "name": s.get("name")}
                        for s in data.get("courses", [])
                    ]
                }

            elif tool_name == "convertkit_unsubscribe":
                payload = {
                    "api_secret": api_secret or api_key,
                    "email": arguments["email"],
                }
                r = await c.put("/unsubscribe", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"subscriber": data.get("subscriber", {})}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("convertkit_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
