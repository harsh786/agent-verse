"""GetResponse MCP server — email marketing contacts, campaigns, and statistics.

Environment:
  GETRESPONSE_API_KEY: GetResponse API key from Integrations & API > API
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.getresponse.com/v3"


def _headers() -> dict[str, str]:
    return {
        "X-Auth-Token": f"api-key {os.getenv('GETRESPONSE_API_KEY', '')}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "getresponse_list_contacts",
        "description": "List contacts in a GetResponse account with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "query[email]": {"type": "string", "description": "Filter contacts by email address"},
                "page": {"type": "integer", "description": "Page number (1-based)", "default": 1},
                "perPage": {"type": "integer", "description": "Contacts per page (max 1000)", "default": 100},
            },
        },
    },
    {
        "name": "getresponse_create_contact",
        "description": "Create a new contact in GetResponse and add them to a campaign list",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "campaign_id": {"type": "string", "description": "GetResponse campaign (list) ID to add the contact to"},
                "name": {"type": "string", "description": "Contact full name"},
                "dayOfCycle": {"type": "integer", "description": "Day of autoresponder cycle to place contact on", "default": 0},
            },
            "required": ["email", "campaign_id"],
        },
    },
    {
        "name": "getresponse_get_lists",
        "description": "Retrieve all campaign lists in a GetResponse account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number", "default": 1},
                "perPage": {"type": "integer", "description": "Lists per page", "default": 100},
            },
        },
    },
    {
        "name": "getresponse_create_campaign",
        "description": "Create a new campaign (list) in GetResponse",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Campaign name (unique, alphanumeric+underscore)"},
                "language_code": {"type": "string", "description": "ISO 639-1 language code", "default": "EN"},
                "from_email": {"type": "string", "description": "Sender email address"},
                "from_name": {"type": "string", "description": "Sender display name"},
                "reply_to_email": {"type": "string", "description": "Reply-to email address"},
            },
            "required": ["name", "from_email", "from_name", "reply_to_email"],
        },
    },
    {
        "name": "getresponse_send_newsletter",
        "description": "Create and send a newsletter to a GetResponse campaign list",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Target campaign (list) ID"},
                "subject": {"type": "string", "description": "Newsletter subject line"},
                "html_content": {"type": "string", "description": "HTML body of the newsletter"},
                "from_field_id": {"type": "string", "description": "GetResponse from-field ID for sender"},
            },
            "required": ["campaign_id", "subject", "html_content"],
        },
    },
    {
        "name": "getresponse_get_statistics",
        "description": "Retrieve account-level email delivery and engagement statistics",
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "date_to": {"type": "string", "description": "End date in YYYY-MM-DD format"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("GETRESPONSE_API_KEY"):
        return {"error": "GETRESPONSE_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "getresponse_list_contacts":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "perPage": arguments.get("perPage", 100),
                }
                if "query[email]" in arguments:
                    params["query[email]"] = arguments["query[email]"]
                r = await client.get(f"{BASE_URL}/contacts", headers=_headers(), params=params)
                r.raise_for_status()
                contacts = r.json()
                return {
                    "contacts": [
                        {"contactId": c.get("contactId"), "email": c.get("email"), "name": c.get("name")}
                        for c in (contacts if isinstance(contacts, list) else [])
                    ]
                }

            elif tool_name == "getresponse_create_contact":
                payload: dict[str, Any] = {
                    "email": arguments["email"],
                    "campaign": {"campaignId": arguments["campaign_id"]},
                    "dayOfCycle": arguments.get("dayOfCycle", 0),
                }
                if "name" in arguments:
                    payload["name"] = arguments["name"]
                r = await client.post(f"{BASE_URL}/contacts", headers=_headers(), json=payload)
                return {"success": r.status_code in (200, 202, 204), "status_code": r.status_code}

            elif tool_name == "getresponse_get_lists":
                r = await client.get(
                    f"{BASE_URL}/campaigns",
                    headers=_headers(),
                    params={"page": arguments.get("page", 1), "perPage": arguments.get("perPage", 100)},
                )
                r.raise_for_status()
                campaigns = r.json()
                return {
                    "lists": [
                        {"campaignId": c.get("campaignId"), "name": c.get("name")}
                        for c in (campaigns if isinstance(campaigns, list) else [])
                    ]
                }

            elif tool_name == "getresponse_create_campaign":
                payload = {
                    "name": arguments["name"],
                    "languageCode": arguments.get("language_code", "EN"),
                    "fromField": {"email": arguments["from_email"], "name": arguments["from_name"]},
                    "replyTo": {"email": arguments["reply_to_email"]},
                }
                r = await client.post(f"{BASE_URL}/campaigns", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "getresponse_send_newsletter":
                payload = {
                    "subject": arguments["subject"],
                    "content": {"html": arguments["html_content"]},
                    "sendSettings": {
                        "selectedCampaigns": [{"campaignId": arguments["campaign_id"]}],
                    },
                    "sendOn": "now",
                }
                if "from_field_id" in arguments:
                    payload["fromField"] = {"fromFieldId": arguments["from_field_id"]}
                r = await client.post(f"{BASE_URL}/newsletters", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "getresponse_get_statistics":
                params = {}
                if "date_from" in arguments:
                    params["dateFrom"] = arguments["date_from"]
                if "date_to" in arguments:
                    params["dateTo"] = arguments["date_to"]
                r = await client.get(f"{BASE_URL}/statistics/emails", headers=_headers(), params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("getresponse_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
