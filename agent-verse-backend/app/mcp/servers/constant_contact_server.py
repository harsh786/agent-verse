"""Constant Contact MCP server — email marketing contacts, lists, and campaigns.

Environment:
  CONSTANT_CONTACT_API_KEY: OAuth2 access token from Constant Contact developer account
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.cc.email/v3"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('CONSTANT_CONTACT_API_KEY', '')}",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "constant_contact_list_contacts",
        "description": "List contacts in Constant Contact with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max contacts to return (max 500)", "default": 50},
                "email": {"type": "string", "description": "Filter by email address"},
                "status": {"type": "string", "description": "Filter by status: active, unsubscribed, removed, deleted"},
            },
        },
    },
    {
        "name": "constant_contact_create_contact",
        "description": "Create a new contact in Constant Contact",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "first_name": {"type": "string", "description": "Contact first name"},
                "last_name": {"type": "string", "description": "Contact last name"},
                "phone_number": {"type": "string", "description": "Contact phone number"},
                "list_memberships": {"type": "array", "items": {"type": "string"}, "description": "List UUIDs to add the contact to"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "constant_contact_update_contact",
        "description": "Update an existing Constant Contact contact by contact ID",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "Constant Contact contact UUID"},
                "first_name": {"type": "string", "description": "Updated first name"},
                "last_name": {"type": "string", "description": "Updated last name"},
                "phone_number": {"type": "string", "description": "Updated phone number"},
                "list_memberships": {"type": "array", "items": {"type": "string"}, "description": "Updated list UUID memberships"},
            },
            "required": ["contact_id"],
        },
    },
    {
        "name": "constant_contact_list_contact_lists",
        "description": "Retrieve all contact lists in the Constant Contact account",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max lists to return", "default": 50},
            },
        },
    },
    {
        "name": "constant_contact_create_email_campaign",
        "description": "Create a new email campaign in Constant Contact",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Internal campaign name"},
                "email_campaign_activities": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of campaign activity objects with format, subject, from_name, from_email, html_content",
                },
            },
            "required": ["name", "email_campaign_activities"],
        },
    },
    {
        "name": "constant_contact_get_campaign_activity_summary",
        "description": "Get open, click, bounce, and unsubscribe stats for a campaign activity",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_activity_id": {"type": "string", "description": "Constant Contact campaign activity UUID"},
            },
            "required": ["campaign_activity_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("CONSTANT_CONTACT_API_KEY"):
        return {"error": "CONSTANT_CONTACT_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "constant_contact_list_contacts":
                params: dict[str, Any] = {"limit": arguments.get("limit", 50)}
                if "email" in arguments:
                    params["email"] = arguments["email"]
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(
                    f"{BASE_URL}/contacts",
                    headers=_headers(),
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "contacts": [
                        {
                            "contact_id": c.get("contact_id"),
                            "email": c.get("email_address", {}).get("address", ""),
                            "first_name": c.get("first_name", ""),
                            "last_name": c.get("last_name", ""),
                        }
                        for c in data.get("contacts", [])
                    ]
                }

            elif tool_name == "constant_contact_create_contact":
                payload: dict[str, Any] = {
                    "email_address": {"address": arguments["email"], "permission_to_send": "implicit"},
                }
                for field in ("first_name", "last_name", "phone_number"):
                    if field in arguments:
                        payload[field] = arguments[field]
                if "list_memberships" in arguments:
                    payload["list_memberships"] = arguments["list_memberships"]
                r = await client.post(f"{BASE_URL}/contacts", headers=_headers(), json=payload)
                r.raise_for_status()
                c = r.json()
                return {"contact_id": c.get("contact_id"), "email": c.get("email_address", {}).get("address")}

            elif tool_name == "constant_contact_update_contact":
                contact_id = arguments["contact_id"]
                # Fetch existing first to build complete update
                get_r = await client.get(f"{BASE_URL}/contacts/{contact_id}", headers=_headers())
                get_r.raise_for_status()
                existing = get_r.json()
                for field in ("first_name", "last_name", "phone_number"):
                    if field in arguments:
                        existing[field] = arguments[field]
                if "list_memberships" in arguments:
                    existing["list_memberships"] = arguments["list_memberships"]
                r = await client.put(f"{BASE_URL}/contacts/{contact_id}", headers=_headers(), json=existing)
                r.raise_for_status()
                c = r.json()
                return {"contact_id": c.get("contact_id")}

            elif tool_name == "constant_contact_list_contact_lists":
                r = await client.get(
                    f"{BASE_URL}/contact_lists",
                    headers=_headers(),
                    params={"limit": arguments.get("limit", 50)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "lists": [
                        {"list_id": lst.get("list_id"), "name": lst.get("name"), "membership_count": lst.get("membership_count")}
                        for lst in data.get("lists", [])
                    ]
                }

            elif tool_name == "constant_contact_create_email_campaign":
                r = await client.post(
                    f"{BASE_URL}/emails",
                    headers=_headers(),
                    json={
                        "name": arguments["name"],
                        "email_campaign_activities": arguments["email_campaign_activities"],
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "constant_contact_get_campaign_activity_summary":
                r = await client.get(
                    f"{BASE_URL}/reports/summary_reports/email_campaign_summaries/{arguments['campaign_activity_id']}",
                    headers=_headers(),
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
        except Exception as exc:
            logger.exception("constant_contact_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
