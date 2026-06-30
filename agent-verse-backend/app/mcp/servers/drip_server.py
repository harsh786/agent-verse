"""Drip MCP server — ecommerce CRM, subscriber management, and email automation.

Environment:
  DRIP_API_TOKEN: Drip API token from Settings > User Info > API Token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.getdrip.com/v2"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('DRIP_API_TOKEN', '')}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "drip_list_subscribers",
        "description": "List subscribers for a Drip account with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Drip account ID"},
                "status": {"type": "string", "description": "Filter by status: active, unsubscribed, do_not_contact, removed"},
                "per_page": {"type": "integer", "description": "Results per page (max 1000)", "default": 100},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "drip_create_subscriber",
        "description": "Create or update a subscriber in Drip",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Drip account ID"},
                "email": {"type": "string", "description": "Subscriber email address"},
                "first_name": {"type": "string", "description": "Subscriber first name"},
                "last_name": {"type": "string", "description": "Subscriber last name"},
                "custom_fields": {"type": "object", "description": "Custom field key-value pairs"},
            },
            "required": ["account_id", "email"],
        },
    },
    {
        "name": "drip_tag_subscriber",
        "description": "Apply a tag to a subscriber in Drip",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Drip account ID"},
                "email": {"type": "string", "description": "Subscriber email address"},
                "tag": {"type": "string", "description": "Tag name to apply"},
            },
            "required": ["account_id", "email", "tag"],
        },
    },
    {
        "name": "drip_untag_subscriber",
        "description": "Remove a tag from a subscriber in Drip",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Drip account ID"},
                "email": {"type": "string", "description": "Subscriber email address"},
                "tag": {"type": "string", "description": "Tag name to remove"},
            },
            "required": ["account_id", "email", "tag"],
        },
    },
    {
        "name": "drip_list_campaigns",
        "description": "List all email campaigns (drip campaigns) for a Drip account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Drip account ID"},
                "status": {"type": "string", "description": "Filter by status: draft, active, paused"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "drip_send_email",
        "description": "Send a one-off email to a subscriber via Drip",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Drip account ID"},
                "email": {"type": "string", "description": "Recipient subscriber email"},
                "from_email": {"type": "string", "description": "Sender email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "html": {"type": "string", "description": "HTML body content"},
            },
            "required": ["account_id", "email", "from_email", "subject", "html"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("DRIP_API_TOKEN"):
        return {"error": "DRIP_API_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "drip_list_subscribers":
                params: dict[str, Any] = {"per_page": arguments.get("per_page", 100)}
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(
                    f"{BASE_URL}/{arguments['account_id']}/subscribers",
                    headers=_headers(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "subscribers": [
                        {"id": s.get("id"), "email": s.get("email"), "status": s.get("status")}
                        for s in data.get("subscribers", [])
                    ],
                    "meta": data.get("meta", {}),
                }

            elif tool_name == "drip_create_subscriber":
                payload: dict[str, Any] = {"email": arguments["email"]}
                for field in ("first_name", "last_name"):
                    if field in arguments:
                        payload[field] = arguments[field]
                if "custom_fields" in arguments:
                    payload["custom_fields"] = arguments["custom_fields"]
                r = await client.post(
                    f"{BASE_URL}/{arguments['account_id']}/subscribers",
                    headers=_headers(),
                    json={"subscribers": [payload]},
                )
                r.raise_for_status()
                data = r.json()
                subs = data.get("subscribers", [{}])
                return {"id": subs[0].get("id") if subs else None, "email": arguments["email"]}

            elif tool_name == "drip_tag_subscriber":
                r = await client.post(
                    f"{BASE_URL}/{arguments['account_id']}/tags",
                    headers=_headers(),
                    json={"tags": [{"email": arguments["email"], "tag": arguments["tag"]}]},
                )
                return {"success": r.status_code in (200, 201, 204), "status_code": r.status_code}

            elif tool_name == "drip_untag_subscriber":
                r = await client.delete(
                    f"{BASE_URL}/{arguments['account_id']}/subscribers/{arguments['email']}/tags/{arguments['tag']}",
                    headers=_headers(),
                )
                return {"success": r.status_code in (200, 204), "status_code": r.status_code}

            elif tool_name == "drip_list_campaigns":
                params = {}
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(
                    f"{BASE_URL}/{arguments['account_id']}/campaigns",
                    headers=_headers(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "campaigns": [
                        {"id": c.get("id"), "name": c.get("name"), "status": c.get("status")}
                        for c in data.get("campaigns", [])
                    ]
                }

            elif tool_name == "drip_send_email":
                r = await client.post(
                    f"{BASE_URL}/{arguments['account_id']}/messages",
                    headers=_headers(),
                    json={
                        "messages": [
                            {
                                "to": arguments["email"],
                                "from": arguments["from_email"],
                                "subject": arguments["subject"],
                                "html": arguments["html"],
                            }
                        ]
                    },
                )
                return {"success": r.status_code in (200, 201, 204), "status_code": r.status_code}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("drip_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
