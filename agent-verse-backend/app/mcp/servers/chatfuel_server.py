"""Chatfuel MCP server — chatbot platform for Facebook Messenger and Instagram.

Environment:
  CHATFUEL_TOKEN: Chatfuel API token
  CHATFUEL_BOT_ID: Chatfuel bot ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.chatfuel.com"

TOOL_DEFINITIONS = [
    {
        "name": "chatfuel_send_message",
        "description": "Send a message to a specific Chatfuel bot user by their Messenger user ID",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Facebook Messenger user ID"},
                "messages": {
                    "type": "array",
                    "description": "Array of message objects to send",
                    "items": {"type": "object"},
                },
            },
            "required": ["user_id", "messages"],
        },
    },
    {
        "name": "chatfuel_create_broadcast",
        "description": "Create and schedule a broadcast message to subscribers",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Broadcast name for reference"},
                "messages": {
                    "type": "array",
                    "description": "Messages to send in the broadcast",
                    "items": {"type": "object"},
                },
                "tag": {"type": "string", "description": "Facebook message tag for non-promotional broadcasts"},
                "scheduled_time": {"type": "string", "description": "ISO datetime to send the broadcast"},
            },
            "required": ["name", "messages"],
        },
    },
    {
        "name": "chatfuel_list_users",
        "description": "List bot subscribers with optional attribute filters",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Users per page"},
                "segment": {"type": "string", "description": "Filter by segment name"},
            },
        },
    },
    {
        "name": "chatfuel_update_user_attribute",
        "description": "Set a custom attribute value for a Chatfuel bot user",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Chatfuel user ID"},
                "attribute_name": {"type": "string", "description": "Attribute key to set"},
                "attribute_value": {"type": "string", "description": "Value to set for the attribute"},
            },
            "required": ["user_id", "attribute_name", "attribute_value"],
        },
    },
    {
        "name": "chatfuel_list_flows",
        "description": "List conversation flows (blocks) defined in the Chatfuel bot",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "chatfuel_trigger_flow",
        "description": "Trigger a specific bot flow for a user",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User ID to trigger flow for"},
                "flow_name": {"type": "string", "description": "Name or ID of the flow to trigger"},
            },
            "required": ["user_id", "flow_name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    token = os.getenv("CHATFUEL_TOKEN", "")
    bot_id = os.getenv("CHATFUEL_BOT_ID", "")
    if not token:
        return {"error": "CHATFUEL_TOKEN not configured"}
    if not bot_id:
        return {"error": "CHATFUEL_BOT_ID not configured"}

    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "chatfuel_send_message":
                r = await client.post(
                    f"{BASE_URL}/bots/{bot_id}/users/{arguments['user_id']}/send",
                    headers=headers,
                    params={"chatfuel_token": token},
                    json={"messages": arguments["messages"]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "chatfuel_create_broadcast":
                payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "messages": arguments["messages"],
                }
                if "tag" in arguments:
                    payload["tag"] = arguments["tag"]
                if "scheduled_time" in arguments:
                    payload["scheduled_time"] = arguments["scheduled_time"]
                r = await client.post(
                    f"{BASE_URL}/bots/{bot_id}/broadcasts",
                    headers=headers,
                    params={"chatfuel_token": token},
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "chatfuel_list_users":
                params: dict[str, Any] = {"chatfuel_token": token}
                if "page" in arguments:
                    params["page"] = arguments["page"]
                if "per_page" in arguments:
                    params["count"] = arguments["per_page"]
                r = await client.get(
                    f"{BASE_URL}/bots/{bot_id}/users",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "chatfuel_update_user_attribute":
                r = await client.patch(
                    f"{BASE_URL}/bots/{bot_id}/users/{arguments['user_id']}",
                    headers=headers,
                    params={"chatfuel_token": token},
                    json={arguments["attribute_name"]: arguments["attribute_value"]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "chatfuel_list_flows":
                params = {"chatfuel_token": token}
                if "page" in arguments:
                    params["page"] = arguments["page"]
                r = await client.get(
                    f"{BASE_URL}/bots/{bot_id}/blocks",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "chatfuel_trigger_flow":
                r = await client.post(
                    f"{BASE_URL}/bots/{bot_id}/users/{arguments['user_id']}/send",
                    headers=headers,
                    params={"chatfuel_token": token},
                    json={"chatfuel_flow": arguments["flow_name"]},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
