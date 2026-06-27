"""MailerLite MCP server — subscribers, groups, and campaigns.

Environment:
  MAILERLITE_API_KEY: MailerLite API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

MAILERLITE_BASE = "https://connect.mailerlite.com/api"

TOOL_DEFINITIONS = [
    {
        "name": "mailerlite_list_subscribers",
        "description": "List MailerLite subscribers",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25},
                "page": {"type": "integer", "default": 1},
                "filter_status": {
                    "type": "string",
                    "enum": ["active", "unsubscribed", "bounced", "junk", "unconfirmed"],
                },
            },
        },
    },
    {
        "name": "mailerlite_create_subscriber",
        "description": "Create or update a MailerLite subscriber",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "name": {"type": "string"},
                "fields": {"type": "object", "description": "Custom field values"},
                "groups": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Group IDs to add subscriber to",
                },
            },
            "required": ["email"],
        },
    },
    {
        "name": "mailerlite_list_groups",
        "description": "List all MailerLite groups",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "mailerlite_list_campaigns",
        "description": "List MailerLite campaigns",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25},
                "page": {"type": "integer", "default": 1},
                "filter_status": {
                    "type": "string",
                    "enum": ["sent", "draft", "ready"],
                },
            },
        },
    },
    {
        "name": "mailerlite_delete_subscriber",
        "description": "Delete a MailerLite subscriber by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "subscriber_id": {"type": "string"},
            },
            "required": ["subscriber_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("MAILERLITE_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("MAILERLITE_API_KEY"):
        return {"error": "MAILERLITE_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=MAILERLITE_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "mailerlite_list_subscribers":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 25),
                    "page": arguments.get("page", 1),
                }
                if "filter_status" in arguments:
                    params["filter[status]"] = arguments["filter_status"]
                r = await c.get("/subscribers", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "subscribers": [
                        {
                            "id": s.get("id"),
                            "email": s.get("email"),
                            "status": s.get("status"),
                        }
                        for s in data.get("data", [])
                    ],
                    "total": data.get("meta", {}).get("total", 0),
                }

            elif tool_name == "mailerlite_create_subscriber":
                payload: dict[str, Any] = {"email": arguments["email"]}
                if "name" in arguments:
                    payload["fields"] = payload.get("fields", {})
                    payload["fields"]["name"] = arguments["name"]
                if "fields" in arguments:
                    existing = payload.get("fields", {})
                    existing.update(arguments["fields"])
                    payload["fields"] = existing
                if "groups" in arguments:
                    payload["groups"] = arguments["groups"]
                r = await c.post("/subscribers", json=payload)
                r.raise_for_status()
                data = r.json()
                sub = data.get("data", {})
                return {"id": sub.get("id"), "email": sub.get("email"), "status": sub.get("status")}

            elif tool_name == "mailerlite_list_groups":
                r = await c.get(
                    "/groups",
                    params={
                        "limit": arguments.get("limit", 25),
                        "page": arguments.get("page", 1),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "groups": [
                        {"id": g.get("id"), "name": g.get("name"), "total": g.get("total", 0)}
                        for g in data.get("data", [])
                    ]
                }

            elif tool_name == "mailerlite_list_campaigns":
                params = {
                    "limit": arguments.get("limit", 25),
                    "page": arguments.get("page", 1),
                }
                if "filter_status" in arguments:
                    params["filter[status]"] = arguments["filter_status"]
                r = await c.get("/campaigns", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "campaigns": [
                        {
                            "id": camp.get("id"),
                            "name": camp.get("name"),
                            "status": camp.get("status"),
                        }
                        for camp in data.get("data", [])
                    ]
                }

            elif tool_name == "mailerlite_delete_subscriber":
                r = await c.delete(f"/subscribers/{arguments['subscriber_id']}")
                return {"success": r.status_code == 204, "status_code": r.status_code}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("mailerlite_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
