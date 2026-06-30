"""Omnisend MCP server — omnichannel marketing contacts, segments, campaigns, and event tracking.

Environment:
  OMNISEND_API_KEY: Omnisend API key from Store Settings > API Keys
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.omnisend.com/v3"


def _headers() -> dict[str, str]:
    return {
        "X-API-KEY": os.getenv("OMNISEND_API_KEY", ""),
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "omnisend_list_contacts",
        "description": "List contacts in an Omnisend account with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max contacts to return", "default": 100},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
                "email": {"type": "string", "description": "Filter by email address"},
            },
        },
    },
    {
        "name": "omnisend_create_contact",
        "description": "Create a new contact in Omnisend",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "first_name": {"type": "string", "description": "Contact first name"},
                "last_name": {"type": "string", "description": "Contact last name"},
                "phone": {"type": "string", "description": "Contact phone number in E.164 format"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to apply to the contact"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "omnisend_add_to_segment",
        "description": "Add a contact to an Omnisend segment by contact ID",
        "parameters": {
            "type": "object",
            "properties": {
                "segment_id": {"type": "string", "description": "Omnisend segment ID"},
                "contact_id": {"type": "string", "description": "Omnisend contact ID"},
            },
            "required": ["segment_id", "contact_id"],
        },
    },
    {
        "name": "omnisend_list_campaigns",
        "description": "List all email campaigns in an Omnisend account",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: draft, scheduled, ongoing, paused, sent"},
                "limit": {"type": "integer", "description": "Max campaigns to return", "default": 50},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "omnisend_get_campaign_stats",
        "description": "Retrieve delivery and engagement statistics for an Omnisend campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Omnisend campaign ID"},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "omnisend_track_event",
        "description": "Track a custom event for a contact in Omnisend for automation triggers",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "event_name": {"type": "string", "description": "Name of the custom event to track"},
                "fields": {"type": "object", "description": "Additional event properties as key-value pairs"},
            },
            "required": ["email", "event_name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("OMNISEND_API_KEY"):
        return {"error": "OMNISEND_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "omnisend_list_contacts":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 100),
                    "offset": arguments.get("offset", 0),
                }
                if "email" in arguments:
                    params["email"] = arguments["email"]
                r = await client.get(f"{BASE_URL}/contacts", headers=_headers(), params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "contacts": [
                        {"contactID": c.get("contactID"), "email": c.get("email"), "status": c.get("status")}
                        for c in data.get("contacts", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "omnisend_create_contact":
                identifiers = [{"type": "email", "id": arguments["email"], "channels": {"email": {"status": "subscribed", "statusDate": ""}}}]
                payload: dict[str, Any] = {"identifiers": identifiers}
                if "first_name" in arguments:
                    payload["firstName"] = arguments["first_name"]
                if "last_name" in arguments:
                    payload["lastName"] = arguments["last_name"]
                if "phone" in arguments:
                    payload["phone"] = arguments["phone"]
                if "tags" in arguments:
                    payload["tags"] = arguments["tags"]
                r = await client.post(f"{BASE_URL}/contacts", headers=_headers(), json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "omnisend_add_to_segment":
                r = await client.post(
                    f"{BASE_URL}/segments/{arguments['segment_id']}/contacts",
                    headers=_headers(),
                    json={"contactID": arguments["contact_id"]},
                )
                return {"success": r.status_code in (200, 201, 204), "status_code": r.status_code}

            elif tool_name == "omnisend_list_campaigns":
                params = {
                    "limit": arguments.get("limit", 50),
                    "offset": arguments.get("offset", 0),
                }
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(f"{BASE_URL}/campaigns", headers=_headers(), params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "campaigns": [
                        {"campaignID": c.get("campaignID"), "name": c.get("name"), "status": c.get("status")}
                        for c in data.get("campaigns", [])
                    ]
                }

            elif tool_name == "omnisend_get_campaign_stats":
                r = await client.get(
                    f"{BASE_URL}/campaigns/{arguments['campaign_id']}/statistics",
                    headers=_headers(),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "omnisend_track_event":
                payload = {
                    "email": arguments["email"],
                    "eventName": arguments["event_name"],
                    "fields": arguments.get("fields", {}),
                }
                r = await client.post(f"{BASE_URL}/events", headers=_headers(), json=payload)
                return {"success": r.status_code in (200, 201, 204), "status_code": r.status_code}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("omnisend_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
