"""Front MCP server — shared inbox and customer communication platform.

Environment:
  FRONT_API_TOKEN: Front API token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

FRONT_BASE = "https://api2.frontapp.com"

TOOL_DEFINITIONS = [
    {
        "name": "front_list_conversations",
        "description": "List Front conversations (shared inbox messages)",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25},
                "page_token": {"type": "string", "description": "Pagination token"},
                "inbox_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["assigned", "unassigned", "archived", "deleted"],
                },
            },
        },
    },
    {
        "name": "front_get_conversation",
        "description": "Get a Front conversation by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "front_send_reply",
        "description": "Send a reply to an existing Front conversation",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
                "body": {"type": "string"},
                "author_id": {"type": "string", "description": "Teammate ID sending the reply"},
                "to": {"type": "array", "items": {"type": "string"}, "description": "Recipient addresses"},
                "cc": {"type": "array", "items": {"type": "string"}},
                "bcc": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["conversation_id", "body"],
        },
    },
    {
        "name": "front_create_message",
        "description": "Create a new outbound message/conversation in Front",
        "parameters": {
            "type": "object",
            "properties": {
                "inbox_id": {"type": "string"},
                "to": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "sender": {"type": "object", "description": "Sender info with email/name"},
            },
            "required": ["inbox_id", "to", "body"],
        },
    },
    {
        "name": "front_update_conversation",
        "description": "Update a Front conversation (assign, archive, tag, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
                "assignee_id": {"type": "string", "description": "Teammate ID to assign"},
                "status": {"type": "string", "enum": ["archived", "deleted", "open"]},
                "tag_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tag IDs to apply",
                },
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "front_list_inboxes",
        "description": "List all Front inboxes (channels)",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "front_list_teammates",
        "description": "List all Front teammates (agents)",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("FRONT_API_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("FRONT_API_TOKEN", "")
    if not token:
        return {"error": "FRONT_API_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(base_url=FRONT_BASE, headers=_headers(), timeout=30.0) as c:
            if tool_name == "front_list_conversations":
                params: dict[str, Any] = {"limit": arguments.get("limit", 25)}
                if pt := arguments.get("page_token"):
                    params["page_token"] = pt
                if status := arguments.get("status"):
                    params["q[statuses][]"] = status
                base_path = (
                    f"/inboxes/{arguments['inbox_id']}/conversations"
                    if (iid := arguments.get("inbox_id"))
                    else "/conversations"
                )
                r = await c.get(base_path, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "front_get_conversation":
                r = await c.get(f"/conversations/{arguments['conversation_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "front_send_reply":
                cid = arguments["conversation_id"]
                payload: dict[str, Any] = {"body": arguments["body"]}
                if author := arguments.get("author_id"):
                    payload["author_id"] = author
                for field in ["to", "cc", "bcc"]:
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"/conversations/{cid}/messages", json=payload)
                r.raise_for_status()
                return {"success": True, "status_code": r.status_code}

            elif tool_name == "front_create_message":
                payload = {
                    "to": arguments["to"],
                    "body": arguments["body"],
                }
                if subj := arguments.get("subject"):
                    payload["subject"] = subj
                if sender := arguments.get("sender"):
                    payload["sender"] = sender
                iid = arguments["inbox_id"]
                r = await c.post(f"/inboxes/{iid}/imported_messages", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "front_update_conversation":
                cid = arguments["conversation_id"]
                payload = {}
                if assignee := arguments.get("assignee_id"):
                    payload["assignee_id"] = assignee
                if status := arguments.get("status"):
                    payload["status"] = status
                if tags := arguments.get("tag_ids"):
                    payload["tag_ids"] = tags
                r = await c.patch(f"/conversations/{cid}", json=payload)
                r.raise_for_status()
                return {"success": True, "status_code": r.status_code}

            elif tool_name == "front_list_inboxes":
                r = await c.get("/inboxes")
                r.raise_for_status()
                return r.json()

            elif tool_name == "front_list_teammates":
                r = await c.get("/teammates")
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("front_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
