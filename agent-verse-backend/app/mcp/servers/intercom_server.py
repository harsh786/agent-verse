"""Intercom MCP server — customers, conversations, notes, tags.

Environment:
  INTERCOM_ACCESS_TOKEN: Intercom OAuth2 / developer access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

INTERCOM_BASE = "https://api.intercom.io"

TOOL_DEFINITIONS = [
    {
        "name": "intercom_list_conversations",
        "description": "List Intercom conversations (newest first)",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "intercom_get_conversation",
        "description": "Get a single Intercom conversation by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "intercom_reply_conversation",
        "description": "Reply to an Intercom conversation",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
                "body": {"type": "string", "description": "Reply message body"},
                "message_type": {
                    "type": "string",
                    "enum": ["comment", "note"],
                    "default": "comment",
                },
                "type": {"type": "string", "default": "admin"},
                "admin_id": {"type": "string", "description": "Admin ID to reply as"},
            },
            "required": ["conversation_id", "body", "admin_id"],
        },
    },
    {
        "name": "intercom_list_users",
        "description": "List Intercom contacts/users",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "intercom_create_user",
        "description": "Create or update an Intercom contact/user",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "name": {"type": "string"},
                "role": {
                    "type": "string",
                    "enum": ["user", "lead"],
                    "default": "user",
                },
                "custom_attributes": {"type": "object"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "intercom_create_note",
        "description": "Add a note to an Intercom contact",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Intercom contact/user ID"},
                "body": {"type": "string"},
                "admin_id": {"type": "string"},
            },
            "required": ["user_id", "body", "admin_id"],
        },
    },
    {
        "name": "intercom_tag_user",
        "description": "Apply a tag to an Intercom user",
        "parameters": {
            "type": "object",
            "properties": {
                "tag_name": {"type": "string"},
                "user_id": {"type": "string", "description": "Intercom contact/user ID"},
            },
            "required": ["tag_name", "user_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("INTERCOM_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Intercom-Version": "2.10",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("INTERCOM_ACCESS_TOKEN"):
        return {"error": "INTERCOM_ACCESS_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=INTERCOM_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "intercom_list_conversations":
                r = await c.get(
                    "/conversations",
                    params={
                        "page": arguments.get("page", 1),
                        "per_page": arguments.get("per_page", 20),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "conversations": [
                        {
                            "id": conv["id"],
                            "state": conv.get("state"),
                            "subject": conv.get("conversation_message", {}).get("subject", ""),
                        }
                        for conv in data.get("conversations", [])
                    ],
                    "total_count": data.get("total_count"),
                }

            elif tool_name == "intercom_get_conversation":
                r = await c.get(f"/conversations/{arguments['conversation_id']}")
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "state": data.get("state"),
                    "created_at": data.get("created_at"),
                    "body": data.get("conversation_message", {}).get("body", "")[:1000],
                }

            elif tool_name == "intercom_reply_conversation":
                payload: dict[str, Any] = {
                    "message_type": arguments.get("message_type", "comment"),
                    "type": arguments.get("type", "admin"),
                    "admin_id": arguments["admin_id"],
                    "body": arguments["body"],
                }
                r = await c.post(
                    f"/conversations/{arguments['conversation_id']}/reply", json=payload
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "state": data.get("state")}

            elif tool_name == "intercom_list_users":
                r = await c.get(
                    "/contacts",
                    params={
                        "page": arguments.get("page", 1),
                        "per_page": arguments.get("per_page", 20),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "users": [
                        {
                            "id": u["id"],
                            "email": u.get("email", ""),
                            "name": u.get("name", ""),
                        }
                        for u in data.get("data", [])
                    ]
                }

            elif tool_name == "intercom_create_user":
                payload = {
                    "email": arguments["email"],
                    "role": arguments.get("role", "user"),
                }
                if "name" in arguments:
                    payload["name"] = arguments["name"]
                if "custom_attributes" in arguments:
                    payload["custom_attributes"] = arguments["custom_attributes"]
                r = await c.post("/contacts", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "email": data.get("email")}

            elif tool_name == "intercom_create_note":
                payload = {
                    "contact_id": arguments["user_id"],
                    "body": arguments["body"],
                    "admin_id": arguments["admin_id"],
                }
                r = await c.post("/notes", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "body": data.get("body", "")[:200]}

            elif tool_name == "intercom_tag_user":
                payload = {
                    "name": arguments["tag_name"],
                    "users": [{"id": arguments["user_id"]}],
                }
                r = await c.post("/tags", json=payload)
                r.raise_for_status()
                data = r.json()
                return {"tag_id": data.get("id"), "name": data.get("name")}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("intercom_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
