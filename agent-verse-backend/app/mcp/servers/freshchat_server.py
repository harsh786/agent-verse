"""Freshchat MCP server — modern customer messaging and live chat support.

Environment:
  FRESHCHAT_API_TOKEN: Freshchat API token for authentication
  FRESHCHAT_DOMAIN: Freshchat account domain (e.g. mycompany)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    domain = os.getenv("FRESHCHAT_DOMAIN", "")
    return f"https://{domain}.freshchat.com/v2" if domain else "https://api.freshchat.com/v2"


TOOL_DEFINITIONS = [
    {
        "name": "freshchat_list_conversations",
        "description": "List conversations in Freshchat with optional status and channel filters",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: new, assigned, resolved"},
                "page": {"type": "integer", "description": "Page number"},
                "items_per_page": {"type": "integer", "description": "Conversations per page"},
                "assigned_agent_id": {"type": "string", "description": "Filter by assigned agent ID"},
            },
        },
    },
    {
        "name": "freshchat_create_conversation",
        "description": "Create a new Freshchat conversation for a user",
        "parameters": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "ID of the Freshchat channel"},
                "user_id": {"type": "string", "description": "ID of the user starting the conversation"},
                "messages": {
                    "type": "array",
                    "description": "Initial messages in the conversation",
                    "items": {"type": "object"},
                },
            },
            "required": ["channel_id", "user_id"],
        },
    },
    {
        "name": "freshchat_send_message",
        "description": "Send a message to an existing Freshchat conversation",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "ID of the target conversation"},
                "message_type": {"type": "string", "description": "Message type: normal or private"},
                "text": {"type": "string", "description": "Text content of the message"},
                "actor_type": {"type": "string", "description": "Who is sending: agent or user"},
                "actor_id": {"type": "string", "description": "ID of the sending agent or user"},
            },
            "required": ["conversation_id", "text"],
        },
    },
    {
        "name": "freshchat_list_agents",
        "description": "List all support agents in the Freshchat account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "items_per_page": {"type": "integer", "description": "Agents per page"},
            },
        },
    },
    {
        "name": "freshchat_get_conversation_stats",
        "description": "Get conversation statistics including response times and resolution rates",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "to_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
            },
        },
    },
    {
        "name": "freshchat_update_status",
        "description": "Update the status of a Freshchat conversation",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "ID of the conversation"},
                "status": {"type": "string", "description": "New status: assigned, resolved, new"},
                "assigned_agent_id": {"type": "string", "description": "Agent ID to assign conversation to"},
            },
            "required": ["conversation_id", "status"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_token = os.getenv("FRESHCHAT_API_TOKEN", "")
    if not api_token:
        return {"error": "FRESHCHAT_API_TOKEN not configured"}

    base_url = _base_url()
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "freshchat_list_conversations":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{base_url}/conversations", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "freshchat_create_conversation":
                payload: dict[str, Any] = {
                    "channel_id": arguments["channel_id"],
                    "users": [{"id": arguments["user_id"]}],
                }
                if "messages" in arguments:
                    payload["messages"] = arguments["messages"]
                r = await client.post(f"{base_url}/conversations", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "freshchat_send_message":
                conv_id = arguments["conversation_id"]
                payload = {
                    "message_type": arguments.get("message_type", "normal"),
                    "message_parts": [{"text": {"content": arguments["text"]}}],
                    "actor_type": arguments.get("actor_type", "agent"),
                }
                if "actor_id" in arguments:
                    payload["actor_id"] = arguments["actor_id"]
                r = await client.post(
                    f"{base_url}/conversations/{conv_id}/messages",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "freshchat_list_agents":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{base_url}/agents", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "freshchat_get_conversation_stats":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{base_url}/reports/overview", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "freshchat_update_status":
                conv_id = arguments["conversation_id"]
                payload = {"status": arguments["status"]}
                if "assigned_agent_id" in arguments:
                    payload["assigned_agent_id"] = arguments["assigned_agent_id"]
                r = await client.patch(
                    f"{base_url}/conversations/{conv_id}",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
