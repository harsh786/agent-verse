"""Telegram MCP server — interact with Telegram Bot API.

Environment:
  TELEGRAM_BOT_TOKEN: Bot token from @BotFather
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "telegram_send_message",
        "description": "Send a text message to a Telegram chat",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "Chat ID or @username"},
                "text": {"type": "string"},
                "parse_mode": {
                    "type": "string",
                    "enum": ["HTML", "Markdown", "MarkdownV2"],
                    "description": "Optional parse mode",
                },
                "reply_to_message_id": {"type": "integer"},
            },
            "required": ["chat_id", "text"],
        },
    },
    {
        "name": "telegram_send_document",
        "description": "Send a document/file to a Telegram chat via URL",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string"},
                "document": {"type": "string", "description": "File URL or file_id"},
                "caption": {"type": "string"},
            },
            "required": ["chat_id", "document"],
        },
    },
    {
        "name": "telegram_send_photo",
        "description": "Send a photo to a Telegram chat via URL",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string"},
                "photo": {"type": "string", "description": "Photo URL or file_id"},
                "caption": {"type": "string"},
            },
            "required": ["chat_id", "photo"],
        },
    },
    {
        "name": "telegram_get_updates",
        "description": "Get pending updates (messages) for the bot",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "description": "Update offset for pagination"},
                "limit": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "telegram_get_chat",
        "description": "Get information about a chat",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string"},
            },
            "required": ["chat_id"],
        },
    },
    {
        "name": "telegram_create_invite_link",
        "description": "Create an invite link for a chat",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string"},
                "name": {"type": "string", "description": "Invite link label"},
                "member_limit": {"type": "integer"},
                "expire_date": {"type": "integer", "description": "Unix timestamp expiry"},
            },
            "required": ["chat_id"],
        },
    },
    {
        "name": "telegram_pin_message",
        "description": "Pin a message in a chat",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string"},
                "message_id": {"type": "integer"},
                "disable_notification": {"type": "boolean", "default": False},
            },
            "required": ["chat_id", "message_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"error": "TELEGRAM_BOT_TOKEN not configured"}

    base = f"https://api.telegram.org/bot{token}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "telegram_send_message":
                payload: dict[str, Any] = {
                    "chat_id": arguments["chat_id"],
                    "text": arguments["text"],
                }
                if "parse_mode" in arguments:
                    payload["parse_mode"] = arguments["parse_mode"]
                if "reply_to_message_id" in arguments:
                    payload["reply_to_message_id"] = arguments["reply_to_message_id"]
                r = await c.post(f"{base}/sendMessage", json=payload)
                r.raise_for_status()
                data = r.json()
                result = data.get("result", {})
                return {
                    "ok": data.get("ok"),
                    "message_id": result.get("message_id"),
                    "chat_id": result.get("chat", {}).get("id"),
                }

            elif tool_name == "telegram_send_document":
                payload = {
                    "chat_id": arguments["chat_id"],
                    "document": arguments["document"],
                }
                if "caption" in arguments:
                    payload["caption"] = arguments["caption"]
                r = await c.post(f"{base}/sendDocument", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"ok": data.get("ok"), "message_id": data.get("result", {}).get("message_id")}

            elif tool_name == "telegram_send_photo":
                payload = {
                    "chat_id": arguments["chat_id"],
                    "photo": arguments["photo"],
                }
                if "caption" in arguments:
                    payload["caption"] = arguments["caption"]
                r = await c.post(f"{base}/sendPhoto", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"ok": data.get("ok"), "message_id": data.get("result", {}).get("message_id")}

            elif tool_name == "telegram_get_updates":
                params: dict[str, Any] = {"limit": arguments.get("limit", 100)}
                if "offset" in arguments:
                    params["offset"] = arguments["offset"]
                r = await c.get(f"{base}/getUpdates", params=params)
                r.raise_for_status()
                data = r.json()
                return {"ok": data.get("ok"), "updates": data.get("result", [])}

            elif tool_name == "telegram_get_chat":
                r = await c.get(
                    f"{base}/getChat", params={"chat_id": arguments["chat_id"]}
                )
                r.raise_for_status()
                data = r.json()
                return data.get("result", {})

            elif tool_name == "telegram_create_invite_link":
                payload = {"chat_id": arguments["chat_id"]}
                for opt in ("name", "member_limit", "expire_date"):
                    if opt in arguments:
                        payload[opt] = arguments[opt]
                r = await c.post(f"{base}/createChatInviteLink", json=payload)
                r.raise_for_status()
                data = r.json()
                return data.get("result", {})

            elif tool_name == "telegram_pin_message":
                payload = {
                    "chat_id": arguments["chat_id"],
                    "message_id": arguments["message_id"],
                    "disable_notification": arguments.get("disable_notification", False),
                }
                r = await c.post(f"{base}/pinChatMessage", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"ok": data.get("ok")}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("telegram_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
