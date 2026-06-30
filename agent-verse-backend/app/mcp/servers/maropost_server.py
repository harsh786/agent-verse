"""Maropost MCP server — email marketing, contact management, and campaign analytics.

Environment:
  MAROPOST_ACCOUNT_ID: Maropost account ID
  MAROPOST_API_KEY: Maropost API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    account_id = os.getenv("MAROPOST_ACCOUNT_ID", "")
    return f"https://api.maropost.com/accounts/{account_id}"


TOOL_DEFINITIONS = [
    {
        "name": "maropost_list_contacts",
        "description": "List contacts in the Maropost account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Contacts per page"},
                "email": {"type": "string", "description": "Filter by email address"},
            },
        },
    },
    {
        "name": "maropost_create_contact",
        "description": "Create or update a contact in Maropost",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "first_name": {"type": "string", "description": "First name"},
                "last_name": {"type": "string", "description": "Last name"},
                "subscribed": {"type": "boolean", "description": "Subscription status"},
                "custom_fields": {"type": "object", "description": "Custom field values"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "maropost_list_lists",
        "description": "List all contact lists in the Maropost account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Lists per page"},
            },
        },
    },
    {
        "name": "maropost_add_to_list",
        "description": "Add a contact to a specific Maropost list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "integer", "description": "List ID to add contact to"},
                "email": {"type": "string", "description": "Contact email"},
                "subscribed": {"type": "boolean", "description": "Subscribe to list"},
            },
            "required": ["list_id", "email"],
        },
    },
    {
        "name": "maropost_send_campaign",
        "description": "Send a triggered campaign to a contact or segment",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID to send"},
                "contact_email": {"type": "string", "description": "Recipient email"},
                "tags": {"type": "object", "description": "Personalization tags"},
            },
            "required": ["campaign_id", "contact_email"],
        },
    },
    {
        "name": "maropost_get_campaign_stats",
        "description": "Get delivery and engagement statistics for a campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "integer", "description": "Campaign ID to get stats for"},
            },
            "required": ["campaign_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    account_id = os.getenv("MAROPOST_ACCOUNT_ID", "")
    api_key = os.getenv("MAROPOST_API_KEY", "")
    if not account_id or not api_key:
        return {"error": "MAROPOST_ACCOUNT_ID and MAROPOST_API_KEY not configured"}

    base_url = _base_url()
    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            def _params(**extra: Any) -> dict[str, Any]:
                return {"auth_token": api_key, **extra}

            if tool_name == "maropost_list_contacts":
                params = _params(**{k: v for k, v in arguments.items() if v is not None})
                r = await client.get(f"{base_url}/contacts.json", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "maropost_create_contact":
                r = await client.post(
                    f"{base_url}/contacts.json",
                    headers=headers,
                    params={"auth_token": api_key},
                    json={"contact": {k: v for k, v in arguments.items() if v is not None}},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "maropost_list_lists":
                params = _params(**{k: v for k, v in arguments.items() if v is not None})
                r = await client.get(f"{base_url}/lists.json", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "maropost_add_to_list":
                list_id = arguments["list_id"]
                r = await client.post(
                    f"{base_url}/lists/{list_id}/contacts.json",
                    headers=headers,
                    params={"auth_token": api_key},
                    json={"contact": {"email": arguments["email"], "subscribed": arguments.get("subscribed", True)}},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "maropost_send_campaign":
                r = await client.post(
                    f"{base_url}/campaigns/{arguments['campaign_id']}/deliver.json",
                    headers=headers,
                    params={"auth_token": api_key},
                    json={
                        "delivery": {
                            "email": arguments["contact_email"],
                            "tags": arguments.get("tags", {}),
                        }
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "maropost_get_campaign_stats":
                r = await client.get(
                    f"{base_url}/campaigns/{arguments['campaign_id']}.json",
                    params={"auth_token": api_key},
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
