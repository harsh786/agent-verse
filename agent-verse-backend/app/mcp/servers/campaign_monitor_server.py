"""Campaign Monitor MCP server — email campaigns, subscriber lists, and delivery stats.

Environment:
  CAMPAIGN_MONITOR_API_KEY: Campaign Monitor API key from Account Settings
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.createsend.com/api/v3.3"


def _auth() -> tuple[str, str]:
    return (os.getenv("CAMPAIGN_MONITOR_API_KEY", ""), "x")


TOOL_DEFINITIONS = [
    {
        "name": "campaign_monitor_list_subscribers",
        "description": "List active subscribers for a specific Campaign Monitor list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Campaign Monitor list ID"},
                "page": {"type": "integer", "description": "Page number (1-based)", "default": 1},
                "page_size": {"type": "integer", "description": "Subscribers per page (max 1000)", "default": 100},
            },
            "required": ["list_id"],
        },
    },
    {
        "name": "campaign_monitor_add_subscriber",
        "description": "Add or update a subscriber on a Campaign Monitor list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Campaign Monitor list ID"},
                "email": {"type": "string", "description": "Subscriber email address"},
                "name": {"type": "string", "description": "Subscriber full name"},
                "resubscribe": {"type": "boolean", "description": "Resubscribe if previously unsubscribed", "default": True},
            },
            "required": ["list_id", "email"],
        },
    },
    {
        "name": "campaign_monitor_create_campaign",
        "description": "Create a new email campaign in Campaign Monitor",
        "parameters": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "Campaign Monitor client ID"},
                "name": {"type": "string", "description": "Campaign name (internal reference)"},
                "subject": {"type": "string", "description": "Email subject line"},
                "from_name": {"type": "string", "description": "Sender display name"},
                "from_email": {"type": "string", "description": "Sender email address"},
                "reply_to": {"type": "string", "description": "Reply-to email address"},
                "html_url": {"type": "string", "description": "URL of HTML email content"},
                "list_ids": {"type": "array", "items": {"type": "string"}, "description": "List IDs to send to"},
            },
            "required": ["client_id", "name", "subject", "from_name", "from_email", "reply_to", "html_url", "list_ids"],
        },
    },
    {
        "name": "campaign_monitor_send_campaign",
        "description": "Send an existing Campaign Monitor campaign immediately or schedule it",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign Monitor campaign ID"},
                "confirmation_email": {"type": "string", "description": "Email to receive send confirmation"},
                "send_date": {"type": "string", "description": "ISO 8601 scheduled date (use 'Immediately' for instant send)", "default": "Immediately"},
            },
            "required": ["campaign_id", "confirmation_email"],
        },
    },
    {
        "name": "campaign_monitor_get_lists",
        "description": "Retrieve all subscriber lists for a Campaign Monitor client",
        "parameters": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "Campaign Monitor client ID"},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "campaign_monitor_get_campaign_summary",
        "description": "Get delivery and engagement summary for a sent Campaign Monitor campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign Monitor campaign ID"},
            },
            "required": ["campaign_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("CAMPAIGN_MONITOR_API_KEY"):
        return {"error": "CAMPAIGN_MONITOR_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "campaign_monitor_list_subscribers":
                r = await client.get(
                    f"{BASE_URL}/lists/{arguments['list_id']}/active.json",
                    auth=_auth(),
                    params={
                        "page": arguments.get("page", 1),
                        "pagesize": arguments.get("page_size", 100),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "subscribers": [
                        {"email": s.get("EmailAddress"), "name": s.get("Name"), "date": s.get("Date")}
                        for s in data.get("Results", [])
                    ],
                    "total_count": data.get("TotalNumberOfRecords", 0),
                }

            elif tool_name == "campaign_monitor_add_subscriber":
                payload: dict[str, Any] = {
                    "EmailAddress": arguments["email"],
                    "Resubscribe": arguments.get("resubscribe", True),
                }
                if "name" in arguments:
                    payload["Name"] = arguments["name"]
                r = await client.post(
                    f"{BASE_URL}/subscribers/{arguments['list_id']}.json",
                    auth=_auth(),
                    json=payload,
                )
                r.raise_for_status()
                return {"email": r.json()}

            elif tool_name == "campaign_monitor_create_campaign":
                r = await client.post(
                    f"{BASE_URL}/campaigns/{arguments['client_id']}.json",
                    auth=_auth(),
                    json={
                        "Name": arguments["name"],
                        "Subject": arguments["subject"],
                        "FromName": arguments["from_name"],
                        "FromEmail": arguments["from_email"],
                        "ReplyTo": arguments["reply_to"],
                        "HtmlUrl": arguments["html_url"],
                        "ListIDs": arguments["list_ids"],
                    },
                )
                r.raise_for_status()
                return {"campaign_id": r.json()}

            elif tool_name == "campaign_monitor_send_campaign":
                r = await client.post(
                    f"{BASE_URL}/campaigns/{arguments['campaign_id']}/send.json",
                    auth=_auth(),
                    json={
                        "ConfirmationEmail": arguments["confirmation_email"],
                        "SendDate": arguments.get("send_date", "Immediately"),
                    },
                )
                return {"success": r.status_code in (200, 202, 204), "status_code": r.status_code}

            elif tool_name == "campaign_monitor_get_lists":
                r = await client.get(
                    f"{BASE_URL}/clients/{arguments['client_id']}/lists.json",
                    auth=_auth(),
                )
                r.raise_for_status()
                lists = r.json()
                return {
                    "lists": [
                        {"id": lst.get("ListID"), "name": lst.get("Name")}
                        for lst in (lists if isinstance(lists, list) else [])
                    ]
                }

            elif tool_name == "campaign_monitor_get_campaign_summary":
                r = await client.get(
                    f"{BASE_URL}/campaigns/{arguments['campaign_id']}/summary.json",
                    auth=_auth(),
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("campaign_monitor_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
