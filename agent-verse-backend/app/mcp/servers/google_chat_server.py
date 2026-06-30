"""Google Chat MCP server — send messages and manage Chat spaces.

Environment:
  GOOGLE_ACCESS_TOKEN: OAuth2 access token with chat.messages scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://chat.googleapis.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "google_chat_list_spaces",
        "description": "List all Google Chat spaces (rooms and direct messages) accessible to the user",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Maximum number of spaces to return"},
                "page_token": {"type": "string", "description": "Pagination token"},
                "filter": {"type": "string", "description": "Filter query (e.g. spaceType = SPACE)"},
            },
        },
    },
    {
        "name": "google_chat_send_message",
        "description": "Send a text message to a Google Chat space or direct message",
        "parameters": {
            "type": "object",
            "properties": {
                "space_name": {"type": "string", "description": "Space name (e.g. spaces/AAAABBBCCC)"},
                "text": {"type": "string", "description": "Text content of the message"},
                "thread_key": {"type": "string", "description": "Thread key to reply in an existing thread"},
                "cards": {
                    "type": "array",
                    "description": "Rich card attachments to include with the message",
                },
            },
            "required": ["space_name", "text"],
        },
    },
    {
        "name": "google_chat_list_messages",
        "description": "List messages in a Google Chat space with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "space_name": {"type": "string", "description": "Space name (e.g. spaces/AAAABBBCCC)"},
                "page_size": {"type": "integer", "description": "Maximum messages to return"},
                "page_token": {"type": "string", "description": "Pagination token"},
                "filter": {"type": "string", "description": "Filter query (e.g. createTime > 2023-01-01T00:00:00Z)"},
            },
            "required": ["space_name"],
        },
    },
    {
        "name": "google_chat_create_space",
        "description": "Create a new Google Chat space (group room)",
        "parameters": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string", "description": "Display name of the new space"},
                "space_type": {"type": "string", "description": "Type: SPACE or GROUP_CHAT"},
                "threaded": {"type": "boolean", "description": "Whether space uses threaded replies"},
            },
            "required": ["display_name"],
        },
    },
    {
        "name": "google_chat_add_member",
        "description": "Add a member (user or bot) to a Google Chat space",
        "parameters": {
            "type": "object",
            "properties": {
                "space_name": {"type": "string", "description": "Space name to add member to"},
                "user_name": {"type": "string", "description": "User resource name (e.g. users/user@example.com)"},
                "role": {"type": "string", "description": "Member role: ROLE_MEMBER or ROLE_MANAGER"},
            },
            "required": ["space_name", "user_name"],
        },
    },
    {
        "name": "google_chat_delete_message",
        "description": "Delete a specific message from a Google Chat space",
        "parameters": {
            "type": "object",
            "properties": {
                "message_name": {"type": "string", "description": "Full message resource name (spaces/*/messages/*)"},
            },
            "required": ["message_name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "GOOGLE_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "google_chat_list_spaces":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/spaces", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "google_chat_send_message":
                space_name = arguments["space_name"]
                payload: dict[str, Any] = {"text": arguments["text"]}
                if "thread_key" in arguments:
                    payload["thread"] = {"threadKey": arguments["thread_key"]}
                if "cards" in arguments:
                    payload["cardsV2"] = arguments["cards"]
                r = await client.post(
                    f"{BASE_URL}/{space_name}/messages",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_chat_list_messages":
                space_name = arguments["space_name"]
                params = {k: v for k, v in arguments.items() if k != "space_name" and v is not None}
                r = await client.get(
                    f"{BASE_URL}/{space_name}/messages",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_chat_create_space":
                payload = {
                    "displayName": arguments["display_name"],
                    "spaceType": arguments.get("space_type", "SPACE"),
                }
                if "threaded" in arguments:
                    payload["spaceThreadingState"] = "THREADED_MESSAGES" if arguments["threaded"] else "UNTHREADED_MESSAGES"
                r = await client.post(f"{BASE_URL}/spaces", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "google_chat_add_member":
                space_name = arguments["space_name"]
                r = await client.post(
                    f"{BASE_URL}/{space_name}/members",
                    headers=headers,
                    json={
                        "member": {
                            "name": arguments["user_name"],
                            "type": "HUMAN",
                        },
                        "role": arguments.get("role", "ROLE_MEMBER"),
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_chat_delete_message":
                r = await client.delete(
                    f"{BASE_URL}/{arguments['message_name']}",
                    headers=headers,
                )
                r.raise_for_status()
                return {"deleted": True}

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
