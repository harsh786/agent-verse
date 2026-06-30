"""Pushover MCP server — push notifications to mobile devices and desktop clients.

Environment:
  PUSHOVER_TOKEN: Pushover application/API token
  PUSHOVER_USER: Pushover user or group key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.pushover.net/1"


TOOL_DEFINITIONS = [
    {
        "name": "pushover_send_notification",
        "description": "Send a push notification to a Pushover user or group",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Notification message body (required)"},
                "title": {"type": "string", "description": "Notification title (defaults to app name)"},
                "url": {"type": "string", "description": "Supplementary URL to show with the message"},
                "url_title": {"type": "string", "description": "Title for the supplementary URL"},
                "priority": {"type": "integer", "description": "Message priority: -2 (lowest) to 2 (emergency)", "default": 0},
                "sound": {"type": "string", "description": "Notification sound name"},
                "device": {"type": "string", "description": "Target specific device name (omit for all devices)"},
                "html": {"type": "integer", "description": "Set to 1 to enable HTML formatting in message", "default": 0},
            },
            "required": ["message"],
        },
    },
    {
        "name": "pushover_get_sounds",
        "description": "Retrieve the list of available notification sound names from Pushover",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "pushover_validate_user",
        "description": "Validate a Pushover user key and optionally a specific device name",
        "parameters": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "Pushover user or group key to validate"},
                "device": {"type": "string", "description": "Optional specific device name to validate"},
            },
            "required": ["user"],
        },
    },
    {
        "name": "pushover_create_delivery_receipt",
        "description": "Retrieve delivery and acknowledgment status for an emergency priority notification",
        "parameters": {
            "type": "object",
            "properties": {
                "receipt": {"type": "string", "description": "Receipt token returned when sending an emergency priority message"},
            },
            "required": ["receipt"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("PUSHOVER_TOKEN", "")
    user = os.getenv("PUSHOVER_USER", "")
    if not token:
        return {"error": "PUSHOVER_TOKEN not configured"}
    if not user:
        return {"error": "PUSHOVER_USER not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "pushover_send_notification":
                payload: dict[str, Any] = {
                    "token": token,
                    "user": user,
                    "message": arguments["message"],
                    "priority": arguments.get("priority", 0),
                    "html": arguments.get("html", 0),
                }
                for field in ("title", "url", "url_title", "sound", "device"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.post(f"{BASE_URL}/messages.json", data=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pushover_get_sounds":
                r = await client.get(
                    f"{BASE_URL}/sounds.json",
                    params={"token": token},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "pushover_validate_user":
                payload = {"token": token, "user": arguments["user"]}
                if "device" in arguments:
                    payload["device"] = arguments["device"]
                r = await client.post(f"{BASE_URL}/users/validate.json", data=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pushover_create_delivery_receipt":
                r = await client.get(
                    f"{BASE_URL}/receipts/{arguments['receipt']}.json",
                    params={"token": token},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("pushover_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
