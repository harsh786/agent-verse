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
        "description": (
            "Send a text message to a Telegram chat. chat_id is optional — when "
            "omitted, the connector's configured Default Chat ID is used. Prefer "
            "plain text; only set parse_mode if you are sure the text is valid "
            "Markdown/HTML (a formatting error auto-falls-back to plain text)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Chat ID or @username. Omit to use the connector default.",
                },
                "text": {"type": "string"},
                "parse_mode": {
                    "type": "string",
                    "enum": ["HTML", "Markdown", "MarkdownV2"],
                    "description": "Optional parse mode",
                },
                "reply_to_message_id": {"type": "integer"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "telegram_send_document",
        "description": "Send a document/file to a Telegram chat via URL. chat_id is "
        "optional — the connector default is used when omitted.",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Omit to use the connector default.",
                },
                "document": {"type": "string", "description": "File URL or file_id"},
                "caption": {"type": "string"},
            },
            "required": ["document"],
        },
    },
    {
        "name": "telegram_send_photo",
        "description": "Send a photo to a Telegram chat via URL. chat_id is optional "
        "— the connector default is used when omitted.",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Omit to use the connector default.",
                },
                "photo": {"type": "string", "description": "Photo URL or file_id"},
                "caption": {"type": "string"},
            },
            "required": ["photo"],
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


def _resolve_token(credentials: dict[str, str] | None) -> str:
    """Resolve the Telegram bot token from connector credentials (set via the
    Connectors UI), falling back to the TELEGRAM_BOT_TOKEN environment variable."""
    creds = credentials or {}
    for name in ("api_key", "token", "bot_token", "TELEGRAM_BOT_TOKEN"):
        val = creds.get(name)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def _resolve_default_chat_id(credentials: dict[str, str] | None) -> str:
    """Resolve the connector's configured default chat id (set via the Connectors
    UI), falling back to the TELEGRAM_DEFAULT_CHAT_ID environment variable.

    This is what lets a goal like "message me the result on Telegram" work without
    the agent having to discover a chat_id first — the destination is configured
    once on the connector."""
    creds = credentials or {}
    for name in ("default_chat_id", "chat_id", "DEFAULT_CHAT_ID", "TELEGRAM_DEFAULT_CHAT_ID"):
        val = creds.get(name)
        if isinstance(val, (str, int)) and str(val).strip():
            return str(val).strip()
    return os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "")


def _effective_chat_id(
    arguments: dict[str, Any], credentials: dict[str, str] | None
) -> str:
    """The chat_id to use: the explicit argument when supplied, otherwise the
    connector's configured default."""
    cid = arguments.get("chat_id")
    if isinstance(cid, (str, int)) and str(cid).strip():
        return str(cid).strip()
    return _resolve_default_chat_id(credentials)


_PARSE_ENTITY_HINT = "can't parse entities"


async def _post_with_parse_fallback(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any]
) -> httpx.Response:
    """POST to the Telegram API; if the request fails with a 400 caused by a
    parse-mode entity error (e.g. an underscore in ``foo_bar`` under legacy
    ``Markdown``), retry once as plain text so a formatting glitch never eats
    the whole message."""
    r = await client.post(url, json=payload)
    if (
        r.status_code == 400
        and payload.get("parse_mode")
        and _PARSE_ENTITY_HINT in r.text.lower()
    ):
        plain = {k: v for k, v in payload.items() if k != "parse_mode"}
        r = await client.post(url, json=plain)
    return r


async def call_tool(
    tool_name: str, arguments: dict[str, Any], credentials: dict[str, str] | None = None
) -> dict[str, Any]:
    token = _resolve_token(credentials)
    if not token:
        return {"error": "TELEGRAM_BOT_TOKEN not configured"}

    base = f"https://api.telegram.org/bot{token}"

    # Resolve the destination once: explicit chat_id argument, else the
    # connector's configured default_chat_id.
    chat_id = _effective_chat_id(arguments, credentials)
    _needs_chat = tool_name in {
        "telegram_send_message",
        "telegram_send_document",
        "telegram_send_photo",
        "telegram_get_chat",
        "telegram_create_invite_link",
        "telegram_pin_message",
    }
    if _needs_chat and not chat_id:
        return {
            "error": (
                "chat_id not provided and no default_chat_id is configured for this "
                "connector. Set a Default Chat ID on the Telegram connector, or pass chat_id."
            )
        }

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "telegram_send_message":
                payload: dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": arguments["text"],
                }
                if "parse_mode" in arguments:
                    payload["parse_mode"] = arguments["parse_mode"]
                if "reply_to_message_id" in arguments:
                    payload["reply_to_message_id"] = arguments["reply_to_message_id"]
                r = await _post_with_parse_fallback(c, f"{base}/sendMessage", payload)
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
                    "chat_id": chat_id,
                    "document": arguments["document"],
                }
                if "caption" in arguments:
                    payload["caption"] = arguments["caption"]
                if "parse_mode" in arguments:
                    payload["parse_mode"] = arguments["parse_mode"]
                r = await _post_with_parse_fallback(c, f"{base}/sendDocument", payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "ok": data.get("ok"),
                    "message_id": data.get("result", {}).get("message_id"),
                }

            elif tool_name == "telegram_send_photo":
                payload = {
                    "chat_id": chat_id,
                    "photo": arguments["photo"],
                }
                if "caption" in arguments:
                    payload["caption"] = arguments["caption"]
                if "parse_mode" in arguments:
                    payload["parse_mode"] = arguments["parse_mode"]
                r = await _post_with_parse_fallback(c, f"{base}/sendPhoto", payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "ok": data.get("ok"),
                    "message_id": data.get("result", {}).get("message_id"),
                }

            elif tool_name == "telegram_get_updates":
                params: dict[str, Any] = {"limit": arguments.get("limit", 100)}
                if "offset" in arguments:
                    params["offset"] = arguments["offset"]
                r = await c.get(f"{base}/getUpdates", params=params)
                r.raise_for_status()
                data = r.json()
                return {"ok": data.get("ok"), "updates": data.get("result", [])}

            elif tool_name == "telegram_get_chat":
                r = await c.get(f"{base}/getChat", params={"chat_id": chat_id})
                r.raise_for_status()
                data = r.json()
                return data.get("result", {})

            elif tool_name == "telegram_create_invite_link":
                payload = {"chat_id": chat_id}
                for opt in ("name", "member_limit", "expire_date"):
                    if opt in arguments:
                        payload[opt] = arguments[opt]
                r = await c.post(f"{base}/createChatInviteLink", json=payload)
                r.raise_for_status()
                data = r.json()
                return data.get("result", {})

            elif tool_name == "telegram_pin_message":
                payload = {
                    "chat_id": chat_id,
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
