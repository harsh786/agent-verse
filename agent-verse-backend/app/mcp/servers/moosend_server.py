"""Moosend MCP server — email marketing lists, subscribers, and campaigns.

Environment:
  MOOSEND_API_KEY: Moosend API key from Settings > API Key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.moosend.com/v3"


def _params_with_key(extra: dict | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"apikey": os.getenv("MOOSEND_API_KEY", "")}
    if extra:
        params.update(extra)
    return params


TOOL_DEFINITIONS = [
    {
        "name": "moosend_get_lists",
        "description": "Retrieve all mailing lists in a Moosend account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number (1-based)", "default": 1},
                "page_size": {"type": "integer", "description": "Lists per page", "default": 50},
                "sort_by": {"type": "string", "description": "Field to sort by: CreatedOn, Name"},
            },
        },
    },
    {
        "name": "moosend_create_subscriber",
        "description": "Subscribe an email address to a Moosend mailing list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Moosend mailing list ID (UUID)"},
                "email": {"type": "string", "description": "Subscriber email address"},
                "name": {"type": "string", "description": "Subscriber full name"},
                "custom_fields": {"type": "array", "items": {"type": "string"}, "description": "Custom field values"},
            },
            "required": ["list_id", "email"],
        },
    },
    {
        "name": "moosend_unsubscribe",
        "description": "Unsubscribe an email address from a Moosend mailing list",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Moosend mailing list ID (UUID)"},
                "email": {"type": "string", "description": "Subscriber email address to unsubscribe"},
            },
            "required": ["list_id", "email"],
        },
    },
    {
        "name": "moosend_get_campaigns",
        "description": "List all campaigns in a Moosend account with optional status filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number (1-based)", "default": 1},
                "page_size": {"type": "integer", "description": "Campaigns per page", "default": 50},
            },
        },
    },
    {
        "name": "moosend_create_campaign",
        "description": "Create a new email campaign in Moosend",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Campaign name"},
                "subject": {"type": "string", "description": "Email subject line"},
                "sender_email": {"type": "string", "description": "Sender email address"},
                "sender_name": {"type": "string", "description": "Sender display name"},
                "list_id": {"type": "string", "description": "Mailing list UUID to send to"},
                "html_content": {"type": "string", "description": "HTML email body"},
            },
            "required": ["name", "subject", "sender_email", "sender_name", "list_id"],
        },
    },
    {
        "name": "moosend_get_campaign_stats",
        "description": "Retrieve statistics for a specific Moosend campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Moosend campaign UUID"},
            },
            "required": ["campaign_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("MOOSEND_API_KEY"):
        return {"error": "MOOSEND_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "moosend_get_lists":
                params = _params_with_key({
                    "Page": arguments.get("page", 1),
                    "PageSize": arguments.get("page_size", 50),
                })
                if "sort_by" in arguments:
                    params["SortBy"] = arguments["sort_by"]
                r = await client.get(f"{BASE_URL}/lists.json", params=params)
                r.raise_for_status()
                data = r.json()
                context = data.get("Context", {})
                return {
                    "lists": [
                        {"id": lst.get("ID"), "name": lst.get("Name"), "active_member_count": lst.get("ActiveMemberCount")}
                        for lst in context.get("MailingLists", [])
                    ],
                    "total_page_count": context.get("TotalPageCount"),
                }

            elif tool_name == "moosend_create_subscriber":
                payload: dict[str, Any] = {"Email": arguments["email"]}
                if "name" in arguments:
                    payload["Name"] = arguments["name"]
                if "custom_fields" in arguments:
                    payload["CustomFields"] = arguments["custom_fields"]
                r = await client.post(
                    f"{BASE_URL}/subscribers/{arguments['list_id']}/subscribe.json",
                    params=_params_with_key(),
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("Context", {}).get("ID"), "email": arguments["email"]}

            elif tool_name == "moosend_unsubscribe":
                r = await client.post(
                    f"{BASE_URL}/subscribers/{arguments['list_id']}/unsubscribe.json",
                    params=_params_with_key(),
                    json={"Email": arguments["email"]},
                )
                return {"success": r.status_code in (200, 204), "status_code": r.status_code}

            elif tool_name == "moosend_get_campaigns":
                r = await client.get(
                    f"{BASE_URL}/campaigns.json",
                    params=_params_with_key({
                        "Page": arguments.get("page", 1),
                        "PageSize": arguments.get("page_size", 50),
                    }),
                )
                r.raise_for_status()
                data = r.json()
                context = data.get("Context", {})
                return {
                    "campaigns": [
                        {"id": c.get("ID"), "name": c.get("Name"), "status": c.get("Status")}
                        for c in context.get("Campaigns", [])
                    ]
                }

            elif tool_name == "moosend_create_campaign":
                payload = {
                    "Name": arguments["name"],
                    "Subject": arguments["subject"],
                    "SenderEmail": arguments["sender_email"],
                    "SenderName": arguments["sender_name"],
                    "MailingLists": [{"MailingListID": arguments["list_id"]}],
                }
                if "html_content" in arguments:
                    payload["HtmlBody"] = arguments["html_content"]
                r = await client.post(
                    f"{BASE_URL}/campaigns/create.json",
                    params=_params_with_key(),
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("Context"), "name": arguments["name"]}

            elif tool_name == "moosend_get_campaign_stats":
                r = await client.get(
                    f"{BASE_URL}/campaigns/{arguments['campaign_id']}/stats/count.json",
                    params=_params_with_key(),
                )
                r.raise_for_status()
                return r.json().get("Context", {})

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("moosend_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
