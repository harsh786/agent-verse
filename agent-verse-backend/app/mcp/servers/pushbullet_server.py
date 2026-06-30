"""Pushbullet MCP server — push notifications, links, notes, and file sharing to devices.

Environment:
  PUSHBULLET_API_KEY: Pushbullet access token from account settings
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.pushbullet.com/v2"


def _headers() -> dict[str, str]:
    return {
        "Access-Token": os.getenv("PUSHBULLET_API_KEY", ""),
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "pushbullet_push_note",
        "description": "Push a note (title + body text) to a Pushbullet device or channel",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title"},
                "body": {"type": "string", "description": "Note body text"},
                "device_iden": {"type": "string", "description": "Target device identifier (omit to push to all devices)"},
                "email": {"type": "string", "description": "Target user by email address"},
                "channel_tag": {"type": "string", "description": "Target channel by tag"},
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "pushbullet_push_link",
        "description": "Push a URL link to a Pushbullet device or channel",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Link title"},
                "url": {"type": "string", "description": "URL to push"},
                "body": {"type": "string", "description": "Optional description body"},
                "device_iden": {"type": "string", "description": "Target device identifier"},
                "email": {"type": "string", "description": "Target user by email"},
            },
            "required": ["title", "url"],
        },
    },
    {
        "name": "pushbullet_push_file",
        "description": "Push a file reference to a Pushbullet device (file must be pre-uploaded)",
        "parameters": {
            "type": "object",
            "properties": {
                "file_name": {"type": "string", "description": "Name of the file"},
                "file_type": {"type": "string", "description": "MIME type of the file, e.g. image/png"},
                "file_url": {"type": "string", "description": "URL of the file to push"},
                "body": {"type": "string", "description": "Optional message body accompanying the file"},
                "device_iden": {"type": "string", "description": "Target device identifier"},
            },
            "required": ["file_name", "file_type", "file_url"],
        },
    },
    {
        "name": "pushbullet_list_devices",
        "description": "List all devices registered to the Pushbullet account",
        "parameters": {
            "type": "object",
            "properties": {
                "active": {"type": "boolean", "description": "If true, only return active (non-deleted) devices", "default": True},
            },
        },
    },
    {
        "name": "pushbullet_list_pushes",
        "description": "List recent pushes sent or received on the Pushbullet account",
        "parameters": {
            "type": "object",
            "properties": {
                "modified_after": {"type": "number", "description": "Unix timestamp — only return pushes modified after this time"},
                "limit": {"type": "integer", "description": "Max pushes to return", "default": 100},
            },
        },
    },
    {
        "name": "pushbullet_get_user_info",
        "description": "Retrieve profile information for the authenticated Pushbullet user",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("PUSHBULLET_API_KEY"):
        return {"error": "PUSHBULLET_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "pushbullet_push_note":
                payload: dict[str, Any] = {
                    "type": "note",
                    "title": arguments["title"],
                    "body": arguments["body"],
                }
                for field in ("device_iden", "email", "channel_tag"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.post(f"{BASE_URL}/pushes", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pushbullet_push_link":
                payload = {
                    "type": "link",
                    "title": arguments["title"],
                    "url": arguments["url"],
                }
                if "body" in arguments:
                    payload["body"] = arguments["body"]
                for field in ("device_iden", "email"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.post(f"{BASE_URL}/pushes", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pushbullet_push_file":
                payload = {
                    "type": "file",
                    "file_name": arguments["file_name"],
                    "file_type": arguments["file_type"],
                    "file_url": arguments["file_url"],
                }
                if "body" in arguments:
                    payload["body"] = arguments["body"]
                if "device_iden" in arguments:
                    payload["device_iden"] = arguments["device_iden"]
                r = await client.post(f"{BASE_URL}/pushes", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pushbullet_list_devices":
                params: dict[str, Any] = {}
                if arguments.get("active", True):
                    params["active"] = "true"
                r = await client.get(f"{BASE_URL}/devices", headers=_headers(), params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "devices": [
                        {"iden": d.get("iden"), "nickname": d.get("nickname"), "type": d.get("type"), "active": d.get("active")}
                        for d in data.get("devices", [])
                    ]
                }

            elif tool_name == "pushbullet_list_pushes":
                params = {"limit": arguments.get("limit", 100)}
                if "modified_after" in arguments:
                    params["modified_after"] = arguments["modified_after"]
                r = await client.get(f"{BASE_URL}/pushes", headers=_headers(), params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "pushes": data.get("pushes", []),
                    "cursor": data.get("cursor"),
                }

            elif tool_name == "pushbullet_get_user_info":
                r = await client.get(f"{BASE_URL}/users/me", headers=_headers())
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("pushbullet_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
