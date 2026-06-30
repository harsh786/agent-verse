"""Postmark MCP server — transactional email sending, templates, streams, bounces, and stats.

Environment:
  POSTMARK_SERVER_TOKEN: Postmark server API token from Server Settings > API Tokens
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.postmarkapp.com"


def _headers() -> dict[str, str]:
    return {
        "X-Postmark-Server-Token": os.getenv("POSTMARK_SERVER_TOKEN", ""),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "postmark_send_email",
        "description": "Send a single transactional email via Postmark",
        "parameters": {
            "type": "object",
            "properties": {
                "from_email": {"type": "string", "description": "Sender email address (must be a verified sender signature)"},
                "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
                "subject": {"type": "string", "description": "Email subject line"},
                "html_body": {"type": "string", "description": "HTML email body content"},
                "text_body": {"type": "string", "description": "Plain text email body content"},
                "reply_to": {"type": "string", "description": "Reply-to email address"},
                "message_stream": {"type": "string", "description": "Message stream ID (default: outbound)", "default": "outbound"},
                "tag": {"type": "string", "description": "Optional tag for categorizing the email"},
            },
            "required": ["from_email", "to", "subject"],
        },
    },
    {
        "name": "postmark_send_email_with_template",
        "description": "Send an email using a Postmark template by template ID or alias",
        "parameters": {
            "type": "object",
            "properties": {
                "from_email": {"type": "string", "description": "Verified sender email address"},
                "to": {"type": "string", "description": "Recipient email address"},
                "template_id": {"type": "integer", "description": "Postmark template ID (use instead of template_alias)"},
                "template_alias": {"type": "string", "description": "Postmark template alias (use instead of template_id)"},
                "template_model": {"type": "object", "description": "Key-value pairs to populate template variables"},
                "message_stream": {"type": "string", "description": "Message stream ID", "default": "outbound"},
            },
            "required": ["from_email", "to"],
        },
    },
    {
        "name": "postmark_list_message_streams",
        "description": "List all message streams configured for the Postmark server",
        "parameters": {
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean", "description": "Include archived streams", "default": False},
            },
        },
    },
    {
        "name": "postmark_get_delivery_stats",
        "description": "Retrieve email delivery statistics for the Postmark server",
        "parameters": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Filter stats by a specific tag"},
                "from_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "to_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
            },
        },
    },
    {
        "name": "postmark_list_bounces",
        "description": "List bounced emails with filtering and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of bounces to return (max 500)", "default": 25},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
                "type": {"type": "string", "description": "Bounce type filter: HardBounce, SoftBounce, etc."},
                "email_filter": {"type": "string", "description": "Filter bounces by email address"},
                "message_stream": {"type": "string", "description": "Filter by message stream ID"},
            },
        },
    },
    {
        "name": "postmark_activate_bounce",
        "description": "Reactivate a bounced email address so it can receive emails again",
        "parameters": {
            "type": "object",
            "properties": {
                "bounce_id": {"type": "integer", "description": "Postmark bounce ID to reactivate"},
            },
            "required": ["bounce_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("POSTMARK_SERVER_TOKEN"):
        return {"error": "POSTMARK_SERVER_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "postmark_send_email":
                payload: dict[str, Any] = {
                    "From": arguments["from_email"],
                    "To": arguments["to"],
                    "Subject": arguments["subject"],
                    "MessageStream": arguments.get("message_stream", "outbound"),
                }
                if "html_body" in arguments:
                    payload["HtmlBody"] = arguments["html_body"]
                if "text_body" in arguments:
                    payload["TextBody"] = arguments["text_body"]
                if "reply_to" in arguments:
                    payload["ReplyTo"] = arguments["reply_to"]
                if "tag" in arguments:
                    payload["Tag"] = arguments["tag"]
                r = await client.post(f"{BASE_URL}/email", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "postmark_send_email_with_template":
                payload = {
                    "From": arguments["from_email"],
                    "To": arguments["to"],
                    "TemplateModel": arguments.get("template_model", {}),
                    "MessageStream": arguments.get("message_stream", "outbound"),
                }
                if "template_id" in arguments:
                    payload["TemplateId"] = arguments["template_id"]
                if "template_alias" in arguments:
                    payload["TemplateAlias"] = arguments["template_alias"]
                r = await client.post(f"{BASE_URL}/email/withTemplate", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "postmark_list_message_streams":
                r = await client.get(
                    f"{BASE_URL}/message-streams",
                    headers=_headers(),
                    params={"IncludeArchivedStreams": str(arguments.get("include_archived", False)).lower()},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "streams": [
                        {"ID": s.get("ID"), "Name": s.get("Name"), "MessageStreamType": s.get("MessageStreamType")}
                        for s in data.get("MessageStreams", [])
                    ]
                }

            elif tool_name == "postmark_get_delivery_stats":
                params: dict[str, Any] = {}
                if "tag" in arguments:
                    params["tag"] = arguments["tag"]
                if "from_date" in arguments:
                    params["fromdate"] = arguments["from_date"]
                if "to_date" in arguments:
                    params["todate"] = arguments["to_date"]
                r = await client.get(f"{BASE_URL}/stats/outbound", headers=_headers(), params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "postmark_list_bounces":
                params = {
                    "count": arguments.get("count", 25),
                    "offset": arguments.get("offset", 0),
                }
                for field, api_field in [("type", "type"), ("email_filter", "emailFilter"), ("message_stream", "messagestream")]:
                    if field in arguments:
                        params[api_field] = arguments[field]
                r = await client.get(f"{BASE_URL}/bounces", headers=_headers(), params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "bounces": data.get("Bounces", []),
                    "total_count": data.get("TotalCount", 0),
                }

            elif tool_name == "postmark_activate_bounce":
                r = await client.put(
                    f"{BASE_URL}/bounces/{arguments['bounce_id']}/activate",
                    headers=_headers(),
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("postmark_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
