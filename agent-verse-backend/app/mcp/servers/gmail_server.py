"""Gmail MCP server — email reading, sending, drafts, and label management via Google APIs.

Environment:
  GMAIL_ACCESS_TOKEN: Google OAuth2 access token with gmail.modify scope
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://gmail.googleapis.com/gmail/v1"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('GMAIL_ACCESS_TOKEN', '')}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "gmail_list_messages",
        "description": "List message IDs and thread IDs from the Gmail inbox",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Gmail user ID (use 'me' for authenticated user)", "default": "me"},
                "q": {"type": "string", "description": "Gmail search query, e.g. 'from:alice@example.com is:unread'"},
                "label_ids": {"type": "array", "items": {"type": "string"}, "description": "Filter by label IDs, e.g. [\"INBOX\", \"UNREAD\"]"},
                "max_results": {"type": "integer", "description": "Maximum messages to return", "default": 10},
            },
        },
    },
    {
        "name": "gmail_send_message",
        "description": "Send an email from the authenticated Gmail account",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Gmail user ID", "default": "me"},
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Plain text or HTML email body"},
                "from_email": {"type": "string", "description": "Sender email address"},
                "cc": {"type": "string", "description": "CC email address(es), comma-separated"},
                "bcc": {"type": "string", "description": "BCC email address(es), comma-separated"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_get_message",
        "description": "Retrieve the full content of a specific Gmail message by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Gmail user ID", "default": "me"},
                "message_id": {"type": "string", "description": "Gmail message ID"},
                "format": {"type": "string", "description": "Message format: full, minimal, raw, metadata", "default": "full"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "gmail_create_draft",
        "description": "Create an email draft in Gmail without sending it",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Gmail user ID", "default": "me"},
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Draft email subject"},
                "body": {"type": "string", "description": "Draft email body"},
                "from_email": {"type": "string", "description": "Sender address (optional)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_list_labels",
        "description": "List all labels in the authenticated Gmail account",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Gmail user ID", "default": "me"},
            },
        },
    },
    {
        "name": "gmail_search_messages",
        "description": "Search Gmail messages using Gmail search syntax and return matching messages",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Gmail user ID", "default": "me"},
                "query": {"type": "string", "description": "Gmail search query, e.g. 'subject:invoice after:2024/01/01'"},
                "max_results": {"type": "integer", "description": "Maximum messages to return", "default": 10},
            },
            "required": ["query"],
        },
    },
]


def _build_mime_message(to: str, subject: str, body: str, from_email: str | None = None, cc: str | None = None, bcc: str | None = None) -> str:
    """Build a base64url-encoded RFC 2822 message."""
    headers = []
    if from_email:
        headers.append(f"From: {from_email}")
    headers.append(f"To: {to}")
    if cc:
        headers.append(f"Cc: {cc}")
    if bcc:
        headers.append(f"Bcc: {bcc}")
    headers.append(f"Subject: {subject}")
    headers.append("Content-Type: text/plain; charset=UTF-8")
    headers.append("")
    raw = "\r\n".join(headers) + body
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("GMAIL_ACCESS_TOKEN"):
        return {"error": "GMAIL_ACCESS_TOKEN not configured"}

    user_id = arguments.get("user_id", "me")

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "gmail_list_messages":
                params: dict[str, Any] = {"maxResults": arguments.get("max_results", 10)}
                if "q" in arguments:
                    params["q"] = arguments["q"]
                if "label_ids" in arguments:
                    params["labelIds"] = arguments["label_ids"]
                r = await client.get(
                    f"{BASE_URL}/users/{user_id}/messages",
                    headers=_headers(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "messages": data.get("messages", []),
                    "resultSizeEstimate": data.get("resultSizeEstimate", 0),
                }

            elif tool_name == "gmail_send_message":
                raw = _build_mime_message(
                    to=arguments["to"],
                    subject=arguments["subject"],
                    body=arguments["body"],
                    from_email=arguments.get("from_email"),
                    cc=arguments.get("cc"),
                    bcc=arguments.get("bcc"),
                )
                r = await client.post(
                    f"{BASE_URL}/users/{user_id}/messages/send",
                    headers=_headers(),
                    json={"raw": raw},
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "threadId": data.get("threadId"), "labelIds": data.get("labelIds")}

            elif tool_name == "gmail_get_message":
                r = await client.get(
                    f"{BASE_URL}/users/{user_id}/messages/{arguments['message_id']}",
                    headers=_headers(),
                    params={"format": arguments.get("format", "full")},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "gmail_create_draft":
                raw = _build_mime_message(
                    to=arguments["to"],
                    subject=arguments["subject"],
                    body=arguments["body"],
                    from_email=arguments.get("from_email"),
                )
                r = await client.post(
                    f"{BASE_URL}/users/{user_id}/drafts",
                    headers=_headers(),
                    json={"message": {"raw": raw}},
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id"), "message": data.get("message", {})}

            elif tool_name == "gmail_list_labels":
                r = await client.get(
                    f"{BASE_URL}/users/{user_id}/labels",
                    headers=_headers(),
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "labels": [
                        {"id": lbl.get("id"), "name": lbl.get("name"), "type": lbl.get("type")}
                        for lbl in data.get("labels", [])
                    ]
                }

            elif tool_name == "gmail_search_messages":
                params = {
                    "q": arguments["query"],
                    "maxResults": arguments.get("max_results", 10),
                }
                r = await client.get(
                    f"{BASE_URL}/users/{user_id}/messages",
                    headers=_headers(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "messages": data.get("messages", []),
                    "resultSizeEstimate": data.get("resultSizeEstimate", 0),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("gmail_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
