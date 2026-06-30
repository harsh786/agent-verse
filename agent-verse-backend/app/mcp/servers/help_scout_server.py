"""Help Scout MCP server — customer support conversations and mailbox management.

Environment:
  HELP_SCOUT_API_KEY: Help Scout API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.helpscout.net/v2"

TOOL_DEFINITIONS = [
    {
        "name": "helpscout_list_conversations",
        "description": "List support conversations with optional status and mailbox filters",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: active, closed, pending, spam"},
                "mailbox_id": {"type": "integer", "description": "Filter by mailbox ID"},
                "page": {"type": "integer", "description": "Page number"},
                "sort_field": {"type": "string", "description": "Sort by: createdAt, updatedAt"},
                "sort_order": {"type": "string", "description": "Sort direction: asc or desc"},
            },
        },
    },
    {
        "name": "helpscout_create_conversation",
        "description": "Create a new Help Scout conversation (support ticket)",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Subject of the conversation"},
                "mailbox_id": {"type": "integer", "description": "ID of the mailbox to create it in"},
                "customer_email": {"type": "string", "description": "Customer email address"},
                "text": {"type": "string", "description": "Initial message text"},
                "status": {"type": "string", "description": "Initial status: active, pending"},
            },
            "required": ["subject", "mailbox_id", "customer_email"],
        },
    },
    {
        "name": "helpscout_reply_to_conversation",
        "description": "Send a reply to an existing Help Scout conversation",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "integer", "description": "ID of the conversation"},
                "text": {"type": "string", "description": "Reply message text"},
                "user_id": {"type": "integer", "description": "ID of the agent sending the reply"},
                "status": {"type": "string", "description": "Update conversation status after reply"},
            },
            "required": ["conversation_id", "text"],
        },
    },
    {
        "name": "helpscout_list_mailboxes",
        "description": "List all mailboxes in the Help Scout account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "helpscout_list_customers",
        "description": "Search for and list customers in Help Scout",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Search by customer email address"},
                "page": {"type": "integer", "description": "Page number"},
                "sort_field": {"type": "string", "description": "Sort by: createdAt, updatedAt"},
            },
        },
    },
    {
        "name": "helpscout_get_stats",
        "description": "Get conversation and team performance statistics",
        "parameters": {
            "type": "object",
            "properties": {
                "mailbox_id": {"type": "integer", "description": "Mailbox ID to get stats for"},
                "start": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end": {"type": "string", "description": "End date in YYYY-MM-DD format"},
            },
        },
    },
]


async def _get_token(client: httpx.AsyncClient, api_key: str) -> str:
    r = await client.post(
        f"{BASE_URL}/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": api_key,
            "client_secret": api_key,
        },
    )
    if r.status_code == 200:
        return r.json().get("access_token", "")
    return api_key  # Fall back to using key as bearer token


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("HELP_SCOUT_API_KEY", "")
    if not api_key:
        return {"error": "HELP_SCOUT_API_KEY not configured"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "helpscout_list_conversations":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/conversations", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "helpscout_create_conversation":
                payload: dict[str, Any] = {
                    "subject": arguments["subject"],
                    "mailboxId": arguments["mailbox_id"],
                    "customer": {"email": arguments["customer_email"]},
                    "status": arguments.get("status", "active"),
                }
                if "text" in arguments:
                    payload["threads"] = [{"type": "customer", "text": arguments["text"]}]
                r = await client.post(f"{BASE_URL}/conversations", headers=headers, json=payload)
                r.raise_for_status()
                return r.json() if r.content else {"created": True}

            if tool_name == "helpscout_reply_to_conversation":
                conv_id = arguments["conversation_id"]
                payload = {
                    "type": "reply",
                    "text": arguments["text"],
                }
                if "user_id" in arguments:
                    payload["user"] = {"id": arguments["user_id"]}
                r = await client.post(
                    f"{BASE_URL}/conversations/{conv_id}/threads",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                if "status" in arguments:
                    await client.patch(
                        f"{BASE_URL}/conversations/{conv_id}",
                        headers=headers,
                        json={"status": arguments["status"]},
                    )
                return {"replied": True}

            if tool_name == "helpscout_list_mailboxes":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/mailboxes", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "helpscout_list_customers":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/customers", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "helpscout_get_stats":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/reports/conversation", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
