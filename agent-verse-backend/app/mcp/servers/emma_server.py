"""Emma email marketing MCP server — contacts, groups, mailings, and analytics.

Environment:
  EMMA_ACCOUNT_ID: Emma account ID
  EMMA_PUBLIC_KEY: Emma public API key
  EMMA_PRIVATE_KEY: Emma private API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    account_id = os.getenv("EMMA_ACCOUNT_ID", "")
    return f"https://api.e2ma.net/{account_id}"


TOOL_DEFINITIONS = [
    {
        "name": "emma_list_members",
        "description": "List email subscribers in the Emma account",
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "integer", "description": "Pagination start index"},
                "end": {"type": "integer", "description": "Pagination end index"},
                "deleted": {"type": "boolean", "description": "Include deleted members"},
            },
        },
    },
    {
        "name": "emma_add_member",
        "description": "Add a new subscriber (member) to the Emma list",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Subscriber email address"},
                "fields": {"type": "object", "description": "Custom field name-value pairs"},
                "group_ids": {"type": "array", "description": "Groups to add subscriber to", "items": {"type": "integer"}},
            },
            "required": ["email"],
        },
    },
    {
        "name": "emma_list_groups",
        "description": "List all subscriber groups in the Emma account",
        "parameters": {
            "type": "object",
            "properties": {
                "group_types": {"type": "string", "description": "Filter by group type"},
            },
        },
    },
    {
        "name": "emma_create_mailing",
        "description": "Create a new email mailing (campaign) in Emma",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Internal mailing name"},
                "subject": {"type": "string", "description": "Email subject line"},
                "from_name": {"type": "string", "description": "Sender display name"},
                "from_email": {"type": "string", "description": "Sender email address"},
                "html_body": {"type": "string", "description": "HTML content of the email"},
                "plaintext": {"type": "string", "description": "Plain text version"},
            },
            "required": ["name", "subject", "from_email"],
        },
    },
    {
        "name": "emma_send_mailing",
        "description": "Send or schedule an Emma mailing to selected groups",
        "parameters": {
            "type": "object",
            "properties": {
                "mailing_id": {"type": "integer", "description": "Mailing ID to send"},
                "heads_up_emails": {"type": "array", "description": "Email addresses to notify", "items": {"type": "string"}},
                "recipient_groups": {"type": "array", "description": "Group IDs to send to", "items": {"type": "integer"}},
                "send_at": {"type": "string", "description": "ISO datetime to schedule sending"},
            },
            "required": ["mailing_id"],
        },
    },
    {
        "name": "emma_get_response_stats",
        "description": "Get delivery, open, and click statistics for a sent mailing",
        "parameters": {
            "type": "object",
            "properties": {
                "mailing_id": {"type": "integer", "description": "Mailing ID to get stats for"},
            },
            "required": ["mailing_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    account_id = os.getenv("EMMA_ACCOUNT_ID", "")
    public_key = os.getenv("EMMA_PUBLIC_KEY", "")
    private_key = os.getenv("EMMA_PRIVATE_KEY", "")
    if not account_id or not public_key or not private_key:
        return {"error": "EMMA_ACCOUNT_ID, EMMA_PUBLIC_KEY, and EMMA_PRIVATE_KEY not configured"}

    base_url = _base_url()
    auth = (public_key, private_key)
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "emma_list_members":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{base_url}/members", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "emma_add_member":
                payload: dict[str, Any] = {"email": arguments["email"]}
                if "fields" in arguments:
                    payload["fields"] = arguments["fields"]
                if "group_ids" in arguments:
                    payload["group_ids"] = arguments["group_ids"]
                r = await client.post(f"{base_url}/members/add", auth=auth, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "emma_list_groups":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{base_url}/groups", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "emma_create_mailing":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{base_url}/mailings", auth=auth, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "emma_send_mailing":
                mailing_id = arguments["mailing_id"]
                payload = {}
                if "recipient_groups" in arguments:
                    payload["recipient_groups"] = [{"id": gid} for gid in arguments["recipient_groups"]]
                if "heads_up_emails" in arguments:
                    payload["heads_up_emails"] = arguments["heads_up_emails"]
                if "send_at" in arguments:
                    payload["send_at"] = arguments["send_at"]
                r = await client.post(f"{base_url}/mailings/{mailing_id}", auth=auth, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "emma_get_response_stats":
                r = await client.get(
                    f"{base_url}/response/{arguments['mailing_id']}/summary",
                    auth=auth,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
