"""AWeber MCP server — email marketing subscribers, lists, and broadcasts.

Environment:
  AWEBER_ACCESS_TOKEN: OAuth2 access token from AWeber developer portal
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.aweber.com/1.0"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('AWEBER_ACCESS_TOKEN', '')}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "aweber_list_subscribers",
        "description": "List subscribers for a specific AWeber list",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "AWeber account ID"},
                "list_id": {"type": "string", "description": "AWeber list ID"},
                "page_size": {"type": "integer", "description": "Number of subscribers per page", "default": 100},
            },
            "required": ["account_id", "list_id"],
        },
    },
    {
        "name": "aweber_add_subscriber",
        "description": "Add a new subscriber to an AWeber list",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "AWeber account ID"},
                "list_id": {"type": "string", "description": "AWeber list ID"},
                "email": {"type": "string", "description": "Subscriber email address"},
                "name": {"type": "string", "description": "Subscriber full name"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to apply to subscriber"},
            },
            "required": ["account_id", "list_id", "email"],
        },
    },
    {
        "name": "aweber_remove_subscriber",
        "description": "Unsubscribe a subscriber from an AWeber list",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "AWeber account ID"},
                "list_id": {"type": "string", "description": "AWeber list ID"},
                "subscriber_id": {"type": "string", "description": "AWeber subscriber ID"},
            },
            "required": ["account_id", "list_id", "subscriber_id"],
        },
    },
    {
        "name": "aweber_get_lists",
        "description": "Retrieve all mailing lists for an AWeber account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "AWeber account ID"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "aweber_create_broadcast",
        "description": "Create a broadcast (campaign) for an AWeber list",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "AWeber account ID"},
                "list_id": {"type": "string", "description": "AWeber list ID"},
                "subject": {"type": "string", "description": "Email subject line"},
                "html_body": {"type": "string", "description": "HTML email body content"},
                "from_name": {"type": "string", "description": "Sender display name"},
            },
            "required": ["account_id", "list_id", "subject", "html_body"],
        },
    },
    {
        "name": "aweber_get_account_stats",
        "description": "Retrieve account-level statistics and subscriber counts for AWeber",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "AWeber account ID"},
            },
            "required": ["account_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("AWEBER_ACCESS_TOKEN"):
        return {"error": "AWEBER_ACCESS_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "aweber_list_subscribers":
                r = await client.get(
                    f"{BASE_URL}/accounts/{arguments['account_id']}/lists/{arguments['list_id']}/subscribers",
                    headers=_headers(),
                    params={"ws.size": arguments.get("page_size", 100)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "subscribers": [
                        {"id": s.get("id"), "email": s.get("email"), "status": s.get("status")}
                        for s in data.get("entries", [])
                    ],
                    "total_size": data.get("total_size", 0),
                }

            elif tool_name == "aweber_add_subscriber":
                payload: dict[str, Any] = {
                    "email": arguments["email"],
                    "ws.op": "create",
                }
                if "name" in arguments:
                    payload["name"] = arguments["name"]
                if "tags" in arguments:
                    payload["tags"] = arguments["tags"]
                r = await client.post(
                    f"{BASE_URL}/accounts/{arguments['account_id']}/lists/{arguments['list_id']}/subscribers",
                    headers=_headers(),
                    json=payload,
                )
                r.raise_for_status()
                return {"success": True, "status_code": r.status_code}

            elif tool_name == "aweber_remove_subscriber":
                r = await client.patch(
                    f"{BASE_URL}/accounts/{arguments['account_id']}/lists/{arguments['list_id']}/subscribers/{arguments['subscriber_id']}",
                    headers=_headers(),
                    json={"status": "unsubscribed"},
                )
                r.raise_for_status()
                return {"success": True, "status_code": r.status_code}

            elif tool_name == "aweber_get_lists":
                r = await client.get(
                    f"{BASE_URL}/accounts/{arguments['account_id']}/lists",
                    headers=_headers(),
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "lists": [
                        {"id": lst.get("id"), "name": lst.get("name"), "total_subscribers": lst.get("total_subscribers")}
                        for lst in data.get("entries", [])
                    ]
                }

            elif tool_name == "aweber_create_broadcast":
                payload = {
                    "subject": arguments["subject"],
                    "html_body": arguments["html_body"],
                }
                if "from_name" in arguments:
                    payload["from_name"] = arguments["from_name"]
                r = await client.post(
                    f"{BASE_URL}/accounts/{arguments['account_id']}/lists/{arguments['list_id']}/broadcasts",
                    headers=_headers(),
                    json=payload,
                )
                r.raise_for_status()
                return {"success": True, "status_code": r.status_code, "broadcast": r.json()}

            elif tool_name == "aweber_get_account_stats":
                r = await client.get(
                    f"{BASE_URL}/accounts/{arguments['account_id']}",
                    headers=_headers(),
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("aweber_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
