"""Mailchimp MCP server — lists, campaigns, and members via Mailchimp API v3.

Environment:
  MAILCHIMP_API_KEY: Mailchimp API key (ends with -us1 or similar)
  MAILCHIMP_SERVER_PREFIX: Data center prefix, e.g. "us1" (extracted from API key if omitted)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "mailchimp_list_lists",
        "description": "List all Mailchimp audiences/lists",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "mailchimp_add_member",
        "description": "Subscribe an email address to a Mailchimp list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string"},
                "email": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["subscribed", "pending", "unsubscribed"],
                    "default": "subscribed",
                },
                "merge_fields": {"type": "object", "description": "Merge tag values, e.g. {FNAME: 'John'}"},
            },
            "required": ["list_id", "email"],
        },
    },
    {
        "name": "mailchimp_update_member",
        "description": "Update an existing list member",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string"},
                "email": {"type": "string", "description": "Member email (used to compute subscriber hash)"},
                "status": {
                    "type": "string",
                    "enum": ["subscribed", "unsubscribed", "cleaned", "pending"],
                },
                "merge_fields": {"type": "object"},
            },
            "required": ["list_id", "email"],
        },
    },
    {
        "name": "mailchimp_create_campaign",
        "description": "Create a new Mailchimp email campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["regular", "plaintext", "absplit", "rss", "variate"],
                    "default": "regular",
                },
                "list_id": {"type": "string"},
                "subject_line": {"type": "string"},
                "from_name": {"type": "string"},
                "reply_to": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["list_id", "subject_line", "from_name", "reply_to"],
        },
    },
    {
        "name": "mailchimp_send_campaign",
        "description": "Send a Mailchimp campaign immediately",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "mailchimp_list_members",
        "description": "List members of a Mailchimp list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string"},
                "count": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
                "status": {"type": "string", "enum": ["subscribed", "unsubscribed", "cleaned", "pending"]},
            },
            "required": ["list_id"],
        },
    },
]


def _server_prefix() -> str:
    prefix = os.getenv("MAILCHIMP_SERVER_PREFIX", "")
    if not prefix:
        api_key = os.getenv("MAILCHIMP_API_KEY", "")
        if "-" in api_key:
            prefix = api_key.rsplit("-", 1)[-1]
    return prefix or "us1"


def _base() -> str:
    return f"https://{_server_prefix()}.api.mailchimp.com/3.0"


def _auth() -> tuple[str, str]:
    return ("anystring", os.getenv("MAILCHIMP_API_KEY", ""))


def _subscriber_hash(email: str) -> str:
    import hashlib
    return hashlib.md5(email.lower().encode()).hexdigest()


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("MAILCHIMP_API_KEY"):
        return {"error": "MAILCHIMP_API_KEY not configured"}

    base = _base()

    try:
        async with httpx.AsyncClient(
            auth=_auth(), timeout=30.0
        ) as c:
            if tool_name == "mailchimp_list_lists":
                r = await c.get(
                    f"{base}/lists",
                    params={"count": arguments.get("count", 20), "offset": arguments.get("offset", 0)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "lists": [
                        {"id": lst["id"], "name": lst.get("name"), "member_count": lst.get("stats", {}).get("member_count", 0)}
                        for lst in data.get("lists", [])
                    ],
                    "total_items": data.get("total_items", 0),
                }

            elif tool_name == "mailchimp_add_member":
                payload: dict[str, Any] = {
                    "email_address": arguments["email"],
                    "status": arguments.get("status", "subscribed"),
                }
                if "merge_fields" in arguments:
                    payload["merge_fields"] = arguments["merge_fields"]
                r = await c.post(f"{base}/lists/{arguments['list_id']}/members", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "status": data.get("status"), "email": data.get("email_address")}

            elif tool_name == "mailchimp_update_member":
                sub_hash = _subscriber_hash(arguments["email"])
                payload = {}
                if "status" in arguments:
                    payload["status"] = arguments["status"]
                if "merge_fields" in arguments:
                    payload["merge_fields"] = arguments["merge_fields"]
                r = await c.patch(
                    f"{base}/lists/{arguments['list_id']}/members/{sub_hash}", json=payload
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "status": data.get("status")}

            elif tool_name == "mailchimp_create_campaign":
                payload = {
                    "type": arguments.get("type", "regular"),
                    "recipients": {"list_id": arguments["list_id"]},
                    "settings": {
                        "subject_line": arguments["subject_line"],
                        "from_name": arguments["from_name"],
                        "reply_to": arguments["reply_to"],
                    },
                }
                if "title" in arguments:
                    payload["settings"]["title"] = arguments["title"]
                r = await c.post(f"{base}/campaigns", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "status": data.get("status")}

            elif tool_name == "mailchimp_send_campaign":
                r = await c.post(
                    f"{base}/campaigns/{arguments['campaign_id']}/actions/send"
                )
                return {"success": r.status_code == 204, "status_code": r.status_code}

            elif tool_name == "mailchimp_list_members":
                params: dict[str, Any] = {
                    "count": arguments.get("count", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await c.get(
                    f"{base}/lists/{arguments['list_id']}/members", params=params
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "members": [
                        {
                            "id": m.get("id"),
                            "email": m.get("email_address"),
                            "status": m.get("status"),
                            "full_name": m.get("full_name", ""),
                        }
                        for m in data.get("members", [])
                    ],
                    "total_items": data.get("total_items", 0),
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("mailchimp_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
