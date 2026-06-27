"""WhatsApp Business MCP server — send messages via Meta Cloud API.

Environment:
  WHATSAPP_PHONE_NUMBER_ID: Registered phone number ID
  WHATSAPP_ACCESS_TOKEN: Meta Cloud API access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v18.0"

TOOL_DEFINITIONS = [
    {
        "name": "whatsapp_send_text",
        "description": "Send a plain text WhatsApp message",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient phone number with country code, e.g. 15551234567"},
                "body": {"type": "string", "description": "Message text"},
                "preview_url": {"type": "boolean", "default": False},
            },
            "required": ["to", "body"],
        },
    },
    {
        "name": "whatsapp_send_template",
        "description": "Send an approved WhatsApp message template",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "template_name": {"type": "string"},
                "language_code": {"type": "string", "default": "en_US"},
                "components": {
                    "type": "array",
                    "description": "Template components (header, body, buttons)",
                    "items": {"type": "object"},
                },
            },
            "required": ["to", "template_name"],
        },
    },
    {
        "name": "whatsapp_send_media",
        "description": "Send a media message (image, document, or video)",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "media_type": {
                    "type": "string",
                    "enum": ["image", "document", "video", "audio"],
                },
                "media_url": {"type": "string", "description": "Publicly accessible URL of the media"},
                "caption": {"type": "string"},
                "filename": {"type": "string", "description": "Filename for document type"},
            },
            "required": ["to", "media_type", "media_url"],
        },
    },
    {
        "name": "whatsapp_mark_read",
        "description": "Mark a received WhatsApp message as read",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "wamid of the received message"},
            },
            "required": ["message_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    if not phone_id or not os.getenv("WHATSAPP_ACCESS_TOKEN"):
        return {"error": "WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_ACCESS_TOKEN required"}

    try:
        async with httpx.AsyncClient(
            base_url=GRAPH_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "whatsapp_send_text":
                payload: dict[str, Any] = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": arguments["to"],
                    "type": "text",
                    "text": {
                        "preview_url": arguments.get("preview_url", False),
                        "body": arguments["body"],
                    },
                }
                r = await c.post(f"/{phone_id}/messages", json=payload)
                r.raise_for_status()
                data = r.json()
                msgs = data.get("messages", [{}])
                return {
                    "message_id": msgs[0].get("id") if msgs else None,
                    "to": data.get("contacts", [{}])[0].get("wa_id"),
                }

            elif tool_name == "whatsapp_send_template":
                payload = {
                    "messaging_product": "whatsapp",
                    "to": arguments["to"],
                    "type": "template",
                    "template": {
                        "name": arguments["template_name"],
                        "language": {"code": arguments.get("language_code", "en_US")},
                    },
                }
                if "components" in arguments:
                    payload["template"]["components"] = arguments["components"]
                r = await c.post(f"/{phone_id}/messages", json=payload)
                r.raise_for_status()
                data = r.json()
                msgs = data.get("messages", [{}])
                return {"message_id": msgs[0].get("id") if msgs else None}

            elif tool_name == "whatsapp_send_media":
                mtype = arguments["media_type"]
                media_obj: dict[str, Any] = {"link": arguments["media_url"]}
                if "caption" in arguments:
                    media_obj["caption"] = arguments["caption"]
                if "filename" in arguments and mtype == "document":
                    media_obj["filename"] = arguments["filename"]
                payload = {
                    "messaging_product": "whatsapp",
                    "to": arguments["to"],
                    "type": mtype,
                    mtype: media_obj,
                }
                r = await c.post(f"/{phone_id}/messages", json=payload)
                r.raise_for_status()
                data = r.json()
                msgs = data.get("messages", [{}])
                return {"message_id": msgs[0].get("id") if msgs else None}

            elif tool_name == "whatsapp_mark_read":
                payload = {
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": arguments["message_id"],
                }
                r = await c.post(f"/{phone_id}/messages", json=payload)
                r.raise_for_status()
                return {"success": r.json().get("success", False)}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("whatsapp_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
