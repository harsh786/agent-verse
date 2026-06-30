"""Microsoft Outlook MCP server — email management via Microsoft Graph API.

Environment:
  MICROSOFT_ACCESS_TOKEN: Microsoft OAuth2 access token with Mail.ReadWrite scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GRAPH_ME = "https://graph.microsoft.com/v1.0/me"

TOOL_DEFINITIONS = [
    {
        "name": "outlook_list_messages",
        "description": "List email messages from Outlook inbox",
        "parameters": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "default": "inbox",
                    "description": "Folder name or ID: inbox, sentitems, drafts",
                },
                "top": {"type": "integer", "default": 20, "description": "Number of messages"},
                "filter": {"type": "string", "description": "OData filter expression"},
                "select": {
                    "type": "string",
                    "default": "id,subject,from,receivedDateTime,isRead",
                },
            },
        },
    },
    {
        "name": "outlook_send_email",
        "description": "Send an email via Outlook",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Email body"},
                "body_type": {"type": "string", "enum": ["Text", "HTML"], "default": "Text"},
                "to_recipients": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Recipient email addresses",
                },
                "cc_recipients": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "save_to_sent_items": {"type": "boolean", "default": True},
            },
            "required": ["subject", "to_recipients"],
        },
    },
    {
        "name": "outlook_create_draft",
        "description": "Create a draft email in Outlook without sending",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "body_type": {"type": "string", "enum": ["Text", "HTML"], "default": "Text"},
                "to_recipients": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["subject"],
        },
    },
    {
        "name": "outlook_list_folders",
        "description": "List mail folders in the Outlook account",
        "parameters": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "outlook_search_messages",
        "description": "Search email messages using a keyword query",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "top": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "outlook_list_contacts",
        "description": "List contacts from the Outlook address book",
        "parameters": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "default": 50},
                "search": {"type": "string", "description": "Filter by name or email"},
            },
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _build_recipients(emails: list[str]) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": e}} for e in emails]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("MICROSOFT_ACCESS_TOKEN", "")
    if not token:
        return {"error": "MICROSOFT_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            if tool_name == "outlook_list_messages":
                folder = arguments.get("folder", "inbox")
                params: dict[str, Any] = {
                    "$top": arguments.get("top", 20),
                    "$select": arguments.get("select", "id,subject,from,receivedDateTime,isRead"),
                    "$orderby": "receivedDateTime desc",
                }
                if arguments.get("filter"):
                    params["$filter"] = arguments["filter"]
                r = await c.get(
                    f"{GRAPH_ME}/mailFolders/{folder}/messages",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "messages": [
                        {
                            "id": m.get("id"),
                            "subject": m.get("subject"),
                            "from": m.get("from", {})
                            .get("emailAddress", {})
                            .get("address"),
                            "received_at": m.get("receivedDateTime"),
                            "is_read": m.get("isRead"),
                        }
                        for m in data.get("value", [])
                    ]
                }

            elif tool_name == "outlook_send_email":
                message: dict[str, Any] = {
                    "subject": arguments["subject"],
                    "body": {
                        "contentType": arguments.get("body_type", "Text"),
                        "content": arguments.get("body", ""),
                    },
                    "toRecipients": _build_recipients(arguments["to_recipients"]),
                }
                if arguments.get("cc_recipients"):
                    message["ccRecipients"] = _build_recipients(arguments["cc_recipients"])
                r = await c.post(
                    f"{GRAPH_ME}/sendMail",
                    headers=hdrs,
                    json={
                        "message": message,
                        "saveToSentItems": arguments.get("save_to_sent_items", True),
                    },
                )
                r.raise_for_status()
                return {"sent": True}

            elif tool_name == "outlook_create_draft":
                body: dict[str, Any] = {
                    "subject": arguments["subject"],
                    "body": {
                        "contentType": arguments.get("body_type", "Text"),
                        "content": arguments.get("body", ""),
                    },
                }
                if arguments.get("to_recipients"):
                    body["toRecipients"] = _build_recipients(arguments["to_recipients"])
                r = await c.post(
                    f"{GRAPH_ME}/messages",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "subject": data.get("subject"),
                    "created": True,
                }

            elif tool_name == "outlook_list_folders":
                r = await c.get(
                    f"{GRAPH_ME}/mailFolders",
                    headers=hdrs,
                    params={"$top": arguments.get("top", 20)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "folders": [
                        {
                            "id": f.get("id"),
                            "display_name": f.get("displayName"),
                            "total_items": f.get("totalItemCount"),
                            "unread_items": f.get("unreadItemCount"),
                        }
                        for f in data.get("value", [])
                    ]
                }

            elif tool_name == "outlook_search_messages":
                r = await c.get(
                    f"{GRAPH_ME}/messages",
                    headers=hdrs,
                    params={
                        "$search": f'"{arguments["query"]}"',
                        "$top": arguments.get("top", 20),
                        "$select": "id,subject,from,receivedDateTime,isRead",
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "messages": [
                        {
                            "id": m.get("id"),
                            "subject": m.get("subject"),
                            "from": m.get("from", {})
                            .get("emailAddress", {})
                            .get("address"),
                            "received_at": m.get("receivedDateTime"),
                        }
                        for m in data.get("value", [])
                    ]
                }

            elif tool_name == "outlook_list_contacts":
                params = {"$top": arguments.get("top", 50)}
                if arguments.get("search"):
                    params["$search"] = f'"{arguments["search"]}"'
                r = await c.get(
                    f"{GRAPH_ME}/contacts",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "contacts": [
                        {
                            "id": ct.get("id"),
                            "display_name": ct.get("displayName"),
                            "email_addresses": [
                                e.get("address")
                                for e in ct.get("emailAddresses", [])
                            ],
                            "mobile_phone": ct.get("mobilePhone"),
                        }
                        for ct in data.get("value", [])
                    ]
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("outlook_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
