"""ManyChat MCP server — chat marketing subscriber management and content delivery.

Environment:
  MANYCHAT_API_KEY: ManyChat API key from Settings > API > Access Token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.manychat.com/fb"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('MANYCHAT_API_KEY', '')}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "manychat_get_subscriber_info",
        "description": "Retrieve information about a ManyChat subscriber by their subscriber ID",
        "parameters": {
            "type": "object",
            "properties": {
                "subscriber_id": {"type": "string", "description": "ManyChat subscriber ID"},
            },
            "required": ["subscriber_id"],
        },
    },
    {
        "name": "manychat_create_subscriber",
        "description": "Find or create a ManyChat subscriber by phone number",
        "parameters": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Subscriber phone number in E.164 format"},
                "first_name": {"type": "string", "description": "Subscriber first name"},
                "last_name": {"type": "string", "description": "Subscriber last name"},
                "has_opt_in_sms": {"type": "boolean", "description": "Whether the subscriber has opted in to SMS", "default": True},
            },
            "required": ["phone"],
        },
    },
    {
        "name": "manychat_add_tag",
        "description": "Add a tag to a ManyChat subscriber by tag ID",
        "parameters": {
            "type": "object",
            "properties": {
                "subscriber_id": {"type": "string", "description": "ManyChat subscriber ID"},
                "tag_id": {"type": "integer", "description": "ManyChat tag ID to add"},
            },
            "required": ["subscriber_id", "tag_id"],
        },
    },
    {
        "name": "manychat_remove_tag",
        "description": "Remove a tag from a ManyChat subscriber by tag ID",
        "parameters": {
            "type": "object",
            "properties": {
                "subscriber_id": {"type": "string", "description": "ManyChat subscriber ID"},
                "tag_id": {"type": "integer", "description": "ManyChat tag ID to remove"},
            },
            "required": ["subscriber_id", "tag_id"],
        },
    },
    {
        "name": "manychat_send_content",
        "description": "Send a ManyChat flow or content block to a subscriber",
        "parameters": {
            "type": "object",
            "properties": {
                "subscriber_id": {"type": "string", "description": "ManyChat subscriber ID"},
                "flow_ns": {"type": "string", "description": "ManyChat flow namespace to send"},
            },
            "required": ["subscriber_id", "flow_ns"],
        },
    },
    {
        "name": "manychat_send_dynamic_block",
        "description": "Send a dynamic message block to a ManyChat subscriber",
        "parameters": {
            "type": "object",
            "properties": {
                "subscriber_id": {"type": "string", "description": "ManyChat subscriber ID"},
                "dynamic_block_url": {"type": "string", "description": "URL of the dynamic content block"},
            },
            "required": ["subscriber_id", "dynamic_block_url"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("MANYCHAT_API_KEY"):
        return {"error": "MANYCHAT_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "manychat_get_subscriber_info":
                r = await client.get(
                    f"{BASE_URL}/subscriber/getInfo",
                    headers=_headers(),
                    params={"subscriber_id": arguments["subscriber_id"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "manychat_create_subscriber":
                payload: dict[str, Any] = {
                    "phone": arguments["phone"],
                    "has_opt_in_sms": arguments.get("has_opt_in_sms", True),
                }
                for field in ("first_name", "last_name"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.post(
                    f"{BASE_URL}/subscriber/createSubscriberByPhone",
                    headers=_headers(),
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "manychat_add_tag":
                r = await client.post(
                    f"{BASE_URL}/subscriber/addTag",
                    headers=_headers(),
                    json={"subscriber_id": arguments["subscriber_id"], "tag_id": arguments["tag_id"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "manychat_remove_tag":
                r = await client.post(
                    f"{BASE_URL}/subscriber/removeTag",
                    headers=_headers(),
                    json={"subscriber_id": arguments["subscriber_id"], "tag_id": arguments["tag_id"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "manychat_send_content":
                r = await client.post(
                    f"{BASE_URL}/sending/sendContent",
                    headers=_headers(),
                    json={
                        "subscriber_id": arguments["subscriber_id"],
                        "data": {"version": "v2", "content": {"messages": [{"type": "flow", "flow_ns": arguments["flow_ns"]}]}},
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "manychat_send_dynamic_block":
                r = await client.post(
                    f"{BASE_URL}/sending/sendContent",
                    headers=_headers(),
                    json={
                        "subscriber_id": arguments["subscriber_id"],
                        "data": {
                            "version": "v2",
                            "content": {
                                "messages": [{"type": "dynamic_block_url", "url": arguments["dynamic_block_url"]}]
                            },
                        },
                    },
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("manychat_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
