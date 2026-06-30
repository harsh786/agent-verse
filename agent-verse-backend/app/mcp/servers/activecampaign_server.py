"""ActiveCampaign MCP server — email marketing & CRM contacts, lists, and campaigns.

Environment:
  ACTIVECAMPAIGN_API_KEY: ActiveCampaign API key from Settings > Developer
  ACTIVECAMPAIGN_BASE_URL: Account-specific base URL, e.g. https://youraccountname.api-us1.com
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    return os.getenv("ACTIVECAMPAIGN_BASE_URL", "").rstrip("/") + "/api/3"


def _headers() -> dict[str, str]:
    return {
        "Api-Token": os.getenv("ACTIVECAMPAIGN_API_KEY", ""),
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "activecampaign_list_contacts",
        "description": "List contacts in ActiveCampaign with optional search and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Filter contacts by email address"},
                "limit": {"type": "integer", "description": "Number of contacts to return (max 100)", "default": 20},
                "offset": {"type": "integer", "description": "Number of contacts to skip for pagination", "default": 0},
            },
        },
    },
    {
        "name": "activecampaign_create_contact",
        "description": "Create a new contact in ActiveCampaign",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "first_name": {"type": "string", "description": "Contact first name"},
                "last_name": {"type": "string", "description": "Contact last name"},
                "phone": {"type": "string", "description": "Contact phone number"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "activecampaign_update_contact",
        "description": "Update an existing contact by their ActiveCampaign contact ID",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "ActiveCampaign contact ID"},
                "email": {"type": "string", "description": "Updated email address"},
                "first_name": {"type": "string", "description": "Updated first name"},
                "last_name": {"type": "string", "description": "Updated last name"},
                "phone": {"type": "string", "description": "Updated phone number"},
            },
            "required": ["contact_id"],
        },
    },
    {
        "name": "activecampaign_add_to_list",
        "description": "Add a contact to a specific ActiveCampaign list",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "ActiveCampaign contact ID"},
                "list_id": {"type": "string", "description": "ActiveCampaign list ID"},
                "status": {"type": "integer", "description": "Subscription status: 1=subscribed, 2=unsubscribed", "default": 1},
            },
            "required": ["contact_id", "list_id"],
        },
    },
    {
        "name": "activecampaign_send_campaign",
        "description": "Schedule or send an existing ActiveCampaign email campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "ActiveCampaign campaign ID to send"},
                "scheduled_date": {"type": "string", "description": "ISO 8601 scheduled send datetime (optional, sends immediately if omitted)"},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "activecampaign_get_campaign_stats",
        "description": "Retrieve statistics for a specific ActiveCampaign campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "ActiveCampaign campaign ID"},
            },
            "required": ["campaign_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("ACTIVECAMPAIGN_API_KEY", "")
    base_url = os.getenv("ACTIVECAMPAIGN_BASE_URL", "")
    if not api_key:
        return {"error": "ACTIVECAMPAIGN_API_KEY not configured"}
    if not base_url:
        return {"error": "ACTIVECAMPAIGN_BASE_URL not configured"}

    base = base_url.rstrip("/") + "/api/3"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "activecampaign_list_contacts":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "email" in arguments:
                    params["email"] = arguments["email"]
                r = await client.get(
                    f"{base}/contacts",
                    headers=_headers(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "contacts": [
                        {
                            "id": c["id"],
                            "email": c.get("email", ""),
                            "first_name": c.get("firstName", ""),
                            "last_name": c.get("lastName", ""),
                        }
                        for c in data.get("contacts", [])
                    ],
                    "meta": data.get("meta", {}),
                }

            elif tool_name == "activecampaign_create_contact":
                contact: dict[str, Any] = {"email": arguments["email"]}
                for field, ac_field in [("first_name", "firstName"), ("last_name", "lastName"), ("phone", "phone")]:
                    if field in arguments:
                        contact[ac_field] = arguments[field]
                r = await client.post(
                    f"{base}/contacts",
                    headers=_headers(),
                    json={"contact": contact},
                )
                r.raise_for_status()
                data = r.json()
                c = data.get("contact", {})
                return {"id": c.get("id"), "email": c.get("email")}

            elif tool_name == "activecampaign_update_contact":
                contact = {}
                for field, ac_field in [("email", "email"), ("first_name", "firstName"), ("last_name", "lastName"), ("phone", "phone")]:
                    if field in arguments:
                        contact[ac_field] = arguments[field]
                r = await client.put(
                    f"{base}/contacts/{arguments['contact_id']}",
                    headers=_headers(),
                    json={"contact": contact},
                )
                r.raise_for_status()
                data = r.json()
                c = data.get("contact", {})
                return {"id": c.get("id"), "email": c.get("email")}

            elif tool_name == "activecampaign_add_to_list":
                r = await client.post(
                    f"{base}/contactLists",
                    headers=_headers(),
                    json={
                        "contactList": {
                            "list": arguments["list_id"],
                            "contact": arguments["contact_id"],
                            "status": arguments.get("status", 1),
                        }
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {"contactList": data.get("contactList", {})}

            elif tool_name == "activecampaign_send_campaign":
                payload: dict[str, Any] = {}
                if "scheduled_date" in arguments:
                    payload["campaign"] = {"sdate": arguments["scheduled_date"]}
                r = await client.post(
                    f"{base}/campaigns/{arguments['campaign_id']}/actions/send",
                    headers=_headers(),
                    json=payload or None,
                )
                return {"success": r.status_code in (200, 201, 204), "status_code": r.status_code}

            elif tool_name == "activecampaign_get_campaign_stats":
                r = await client.get(
                    f"{base}/campaigns/{arguments['campaign_id']}/stats",
                    headers=_headers(),
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("activecampaign_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
