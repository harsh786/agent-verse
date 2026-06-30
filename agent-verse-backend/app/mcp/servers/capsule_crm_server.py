"""Capsule CRM MCP server — contacts, opportunities, and notes management.

Environment variables:
  CAPSULE_API_TOKEN: Capsule CRM API token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

CAPSULE_BASE = "https://api.capsulecrm.com/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "capsule_list_contacts",
        "description": "List contacts (people and organisations) in Capsule CRM with optional search and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query to filter contacts"},
                "page": {"type": "integer", "description": "Page number (1-based)", "default": 1},
                "perPage": {"type": "integer", "description": "Results per page (max 100)", "default": 50},
                "embed": {"type": "string", "description": "Comma-separated embedded resources, e.g. 'tags,fields'"},
            },
        },
    },
    {
        "name": "capsule_create_contact",
        "description": "Create a new person or organisation contact in Capsule CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["person", "organisation"],
                    "description": "Contact type",
                    "default": "person",
                },
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "name": {"type": "string", "description": "Organisation name (for type=organisation)"},
                "emailAddresses": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "[{address: 'user@example.com', type: 'Work'}]",
                },
                "phoneNumbers": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "title": {"type": "string"},
                "jobTitle": {"type": "string"},
            },
        },
    },
    {
        "name": "capsule_update_contact",
        "description": "Update an existing Capsule CRM contact by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Capsule contact ID"},
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "jobTitle": {"type": "string"},
                "emailAddresses": {"type": "array", "items": {"type": "object"}},
                "phoneNumbers": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["contact_id"],
        },
    },
    {
        "name": "capsule_list_opportunities",
        "description": "List sales opportunities in Capsule CRM with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query"},
                "page": {"type": "integer", "default": 1},
                "perPage": {"type": "integer", "default": 50},
                "milestone": {"type": "string", "description": "Filter by milestone name"},
            },
        },
    },
    {
        "name": "capsule_create_opportunity",
        "description": "Create a new sales opportunity in Capsule CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Opportunity name/description"},
                "party_id": {"type": "integer", "description": "Associated contact/party ID"},
                "value": {"type": "number", "description": "Opportunity value"},
                "currency": {"type": "string", "description": "ISO currency code, e.g. USD"},
                "expectedCloseOn": {"type": "string", "description": "Expected close date (YYYY-MM-DD)"},
                "milestone": {"type": "string", "description": "Pipeline milestone name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "capsule_add_note",
        "description": "Add a note to a contact or opportunity in Capsule CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "party_id": {"type": "integer", "description": "Contact ID to attach note to"},
                "opportunity_id": {"type": "integer", "description": "Opportunity ID to attach note to"},
                "note": {"type": "string", "description": "Note content"},
            },
            "required": ["note"],
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("CAPSULE_API_TOKEN", "")
    if not api_key:
        return {"error": "CAPSULE_API_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=CAPSULE_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "capsule_list_contacts":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "perPage": arguments.get("perPage", 50),
                }
                if q := arguments.get("q"):
                    params["q"] = q
                if embed := arguments.get("embed"):
                    params["embed"] = embed
                r = await c.get("/parties", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "capsule_create_contact":
                contact_type = arguments.get("type", "person")
                body: dict[str, Any] = {"type": contact_type}
                for k in ("firstName", "lastName", "name", "emailAddresses", "phoneNumbers", "title", "jobTitle"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/parties", json={"party": body})
                r.raise_for_status()
                return r.json()

            elif tool_name == "capsule_update_contact":
                cid = arguments["contact_id"]
                body = {
                    k: arguments[k]
                    for k in ("firstName", "lastName", "jobTitle", "emailAddresses", "phoneNumbers")
                    if k in arguments
                }
                r = await c.patch(f"/parties/{cid}", json={"party": body})
                r.raise_for_status()
                return r.json()

            elif tool_name == "capsule_list_opportunities":
                params = {
                    "page": arguments.get("page", 1),
                    "perPage": arguments.get("perPage", 50),
                }
                if q := arguments.get("q"):
                    params["q"] = q
                if milestone := arguments.get("milestone"):
                    params["milestone"] = milestone
                r = await c.get("/opportunities", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "capsule_create_opportunity":
                body = {"name": arguments["name"]}
                if "party_id" in arguments:
                    body["party"] = {"id": arguments["party_id"]}
                for k in ("value", "currency", "expectedCloseOn", "milestone"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/opportunities", json={"opportunity": body})
                r.raise_for_status()
                return r.json()

            elif tool_name == "capsule_add_note":
                body = {"note": arguments["note"]}
                if "party_id" in arguments:
                    body["party"] = {"id": arguments["party_id"]}
                if "opportunity_id" in arguments:
                    body["opportunity"] = {"id": arguments["opportunity_id"]}
                r = await c.post("/notes", json={"note": body})
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("capsule_crm_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
