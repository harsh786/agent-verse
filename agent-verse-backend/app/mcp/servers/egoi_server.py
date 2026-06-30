"""E-goi MCP server — multi-channel marketing with SMS, email, and push notifications.

Environment:
  EGOI_API_KEY: E-goi API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.egoiapp.com"

TOOL_DEFINITIONS = [
    {
        "name": "egoi_list_lists",
        "description": "List all contact lists in the E-goi account",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "description": "Pagination offset"},
                "limit": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "egoi_add_contact",
        "description": "Add a new contact to an E-goi list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "integer", "description": "E-goi list ID"},
                "email": {"type": "string", "description": "Contact email address"},
                "first_name": {"type": "string", "description": "First name"},
                "last_name": {"type": "string", "description": "Last name"},
                "phone": {"type": "string", "description": "Phone number"},
                "tags": {"type": "array", "description": "Tags to assign", "items": {"type": "integer"}},
            },
            "required": ["list_id", "email"],
        },
    },
    {
        "name": "egoi_list_contacts",
        "description": "List contacts in an E-goi list with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "integer", "description": "E-goi list ID"},
                "offset": {"type": "integer", "description": "Pagination offset"},
                "limit": {"type": "integer", "description": "Results per page"},
                "email": {"type": "string", "description": "Filter by email"},
            },
            "required": ["list_id"],
        },
    },
    {
        "name": "egoi_send_sms",
        "description": "Send a transactional SMS to a contact",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "integer", "description": "Contact list ID"},
                "contact_id": {"type": "string", "description": "Contact ID to send SMS to"},
                "message": {"type": "string", "description": "SMS message text"},
                "sender": {"type": "string", "description": "Sender name or number"},
            },
            "required": ["list_id", "contact_id", "message"],
        },
    },
    {
        "name": "egoi_create_campaign",
        "description": "Create a new email or SMS campaign in E-goi",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "integer", "description": "Target contact list ID"},
                "title": {"type": "string", "description": "Internal campaign title"},
                "subject": {"type": "string", "description": "Email subject line"},
                "from_name": {"type": "string", "description": "Sender display name"},
                "from_email": {"type": "string", "description": "Sender email address"},
            },
            "required": ["list_id", "title"],
        },
    },
    {
        "name": "egoi_get_campaign_stats",
        "description": "Get performance statistics for an E-goi campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_hash": {"type": "string", "description": "Campaign hash ID"},
            },
            "required": ["campaign_hash"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("EGOI_API_KEY", "")
    if not api_key:
        return {"error": "EGOI_API_KEY not configured"}

    headers = {"Apikey": api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "egoi_list_lists":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/lists", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "egoi_add_contact":
                list_id = arguments["list_id"]
                base_field = {"email": arguments["email"]}
                extra: dict[str, Any] = {}
                for k in ("first_name", "last_name", "phone"):
                    if k in arguments:
                        extra[k] = arguments[k]
                r = await client.post(
                    f"{BASE_URL}/lists/{list_id}/contacts",
                    headers=headers,
                    json={"base": base_field, "extra": extra, "tags": arguments.get("tags", [])},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "egoi_list_contacts":
                list_id = arguments["list_id"]
                params = {k: v for k, v in arguments.items() if k != "list_id" and v is not None}
                r = await client.get(
                    f"{BASE_URL}/lists/{list_id}/contacts",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "egoi_send_sms":
                r = await client.post(
                    f"{BASE_URL}/lists/{arguments['list_id']}/contacts/{arguments['contact_id']}/actions/send-sms",
                    headers=headers,
                    json={"message": arguments["message"], "sender": arguments.get("sender", "")},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "egoi_create_campaign":
                r = await client.post(
                    f"{BASE_URL}/campaigns/email",
                    headers=headers,
                    json={
                        "list_id": arguments["list_id"],
                        "internal_name": arguments["title"],
                        "subject": arguments.get("subject", arguments["title"]),
                        "sender_name": arguments.get("from_name", ""),
                        "sender_email": arguments.get("from_email", ""),
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "egoi_get_campaign_stats":
                r = await client.get(
                    f"{BASE_URL}/campaigns/email/{arguments['campaign_hash']}/summary",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
