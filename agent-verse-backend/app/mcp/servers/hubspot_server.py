"""HubSpot MCP server — contacts, companies, deals, notes, CRM search.

Environment variables:
  HUBSPOT_API_KEY: Private App Token (Bearer)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

HUBSPOT_BASE = "https://api.hubapi.com"

TOOL_DEFINITIONS = [
    {
        "name": "hubspot_list_contacts",
        "description": "List HubSpot contacts with optional property filters",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "properties": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Properties to return (e.g. firstname, lastname, email)",
                },
                "after": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "hubspot_get_contact",
        "description": "Get a HubSpot contact by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "properties": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["contact_id"],
        },
    },
    {
        "name": "hubspot_create_contact",
        "description": "Create a new HubSpot contact",
        "parameters": {
            "type": "object",
            "properties": {
                "properties": {
                    "type": "object",
                    "description": "Contact properties, e.g. {email: ..., firstname: ..., lastname: ...}",
                },
            },
            "required": ["properties"],
        },
    },
    {
        "name": "hubspot_update_contact",
        "description": "Update a HubSpot contact's properties",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "properties": {"type": "object"},
            },
            "required": ["contact_id", "properties"],
        },
    },
    {
        "name": "hubspot_list_companies",
        "description": "List HubSpot companies",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "properties": {"type": "array", "items": {"type": "string"}},
                "after": {"type": "string"},
            },
        },
    },
    {
        "name": "hubspot_create_company",
        "description": "Create a new HubSpot company",
        "parameters": {
            "type": "object",
            "properties": {
                "properties": {
                    "type": "object",
                    "description": "Company properties, e.g. {name: ..., domain: ...}",
                },
            },
            "required": ["properties"],
        },
    },
    {
        "name": "hubspot_list_deals",
        "description": "List deals in the HubSpot pipeline",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "properties": {"type": "array", "items": {"type": "string"}},
                "after": {"type": "string"},
            },
        },
    },
    {
        "name": "hubspot_create_deal",
        "description": "Create a new HubSpot deal",
        "parameters": {
            "type": "object",
            "properties": {
                "properties": {
                    "type": "object",
                    "description": "Deal properties, e.g. {dealname: ..., amount: ..., dealstage: ...}",
                },
            },
            "required": ["properties"],
        },
    },
    {
        "name": "hubspot_update_deal",
        "description": "Update a HubSpot deal's stage or properties",
        "parameters": {
            "type": "object",
            "properties": {
                "deal_id": {"type": "string"},
                "properties": {"type": "object"},
            },
            "required": ["deal_id", "properties"],
        },
    },
    {
        "name": "hubspot_create_note",
        "description": "Create a note and associate it with a CRM object",
        "parameters": {
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "Note content"},
                "associations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "to_object_type": {"type": "string"},
                            "to_object_id": {"type": "string"},
                        },
                    },
                    "description": "Objects to associate the note with",
                },
            },
            "required": ["body"],
        },
    },
    {
        "name": "hubspot_search_crm",
        "description": "Search CRM objects using filters",
        "parameters": {
            "type": "object",
            "properties": {
                "object_type": {
                    "type": "string",
                    "enum": ["contacts", "companies", "deals", "tickets"],
                },
                "query": {"type": "string", "description": "Full-text search query"},
                "filters": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Filter groups (HubSpot filter format)",
                },
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["object_type"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("HUBSPOT_API_KEY", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("HUBSPOT_API_KEY", "")
    if not token:
        return {"error": "HUBSPOT_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=HUBSPOT_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "hubspot_list_contacts":
                params: dict[str, Any] = {"limit": arguments.get("limit", 20)}
                if props := arguments.get("properties"):
                    params["properties"] = ",".join(props)
                if after := arguments.get("after"):
                    params["after"] = after
                r = await c.get("/crm/v3/objects/contacts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_get_contact":
                cid = arguments["contact_id"]
                params = {}
                if props := arguments.get("properties"):
                    params["properties"] = ",".join(props)
                r = await c.get(f"/crm/v3/objects/contacts/{cid}", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_create_contact":
                r = await c.post(
                    "/crm/v3/objects/contacts",
                    json={"properties": arguments["properties"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_update_contact":
                cid = arguments["contact_id"]
                r = await c.patch(
                    f"/crm/v3/objects/contacts/{cid}",
                    json={"properties": arguments["properties"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_list_companies":
                params = {"limit": arguments.get("limit", 20)}
                if props := arguments.get("properties"):
                    params["properties"] = ",".join(props)
                if after := arguments.get("after"):
                    params["after"] = after
                r = await c.get("/crm/v3/objects/companies", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_create_company":
                r = await c.post(
                    "/crm/v3/objects/companies",
                    json={"properties": arguments["properties"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_list_deals":
                params = {"limit": arguments.get("limit", 20)}
                if props := arguments.get("properties"):
                    params["properties"] = ",".join(props)
                if after := arguments.get("after"):
                    params["after"] = after
                r = await c.get("/crm/v3/objects/deals", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_create_deal":
                r = await c.post(
                    "/crm/v3/objects/deals",
                    json={"properties": arguments["properties"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_update_deal":
                did = arguments["deal_id"]
                r = await c.patch(
                    f"/crm/v3/objects/deals/{did}",
                    json={"properties": arguments["properties"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_create_note":
                body = arguments["body"]
                props: dict[str, Any] = {"hs_note_body": body}
                payload: dict[str, Any] = {"properties": props}
                if assocs := arguments.get("associations"):
                    payload["associations"] = [
                        {
                            "to": {"id": a["to_object_id"]},
                            "types": [
                                {
                                    "associationCategory": "HUBSPOT_DEFINED",
                                    "associationTypeId": 1,
                                }
                            ],
                        }
                        for a in assocs
                    ]
                r = await c.post("/crm/v3/objects/notes", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "hubspot_search_crm":
                obj = arguments["object_type"]
                search_body: dict[str, Any] = {
                    "limit": arguments.get("limit", 10),
                }
                if q := arguments.get("query"):
                    search_body["query"] = q
                if filters := arguments.get("filters"):
                    search_body["filterGroups"] = [{"filters": f} if isinstance(f, list) else f for f in filters]
                r = await c.post(f"/crm/v3/objects/{obj}/search", json=search_body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("hubspot_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
