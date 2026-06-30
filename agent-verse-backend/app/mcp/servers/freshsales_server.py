"""Freshsales CRM MCP server — contacts, deals, and accounts management.

Environment variables:
  FRESHSALES_API_KEY: Freshsales API key
  FRESHSALES_DOMAIN: Your Freshsales subdomain (e.g. 'mycompany' for mycompany.freshsales.io)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "freshsales_list_contacts",
        "description": "List contacts in Freshsales CRM with optional search and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query to filter contacts"},
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
                "sort": {"type": "string", "description": "Field to sort by, e.g. 'created_at'"},
                "sort_type": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
            },
        },
    },
    {
        "name": "freshsales_create_contact",
        "description": "Create a new contact in Freshsales CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "email": {"type": "string", "description": "Contact's email address"},
                "phone": {"type": "string"},
                "mobile": {"type": "string"},
                "job_title": {"type": "string"},
                "lead_source_id": {"type": "integer"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "freshsales_update_contact",
        "description": "Update an existing Freshsales contact by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Freshsales contact ID"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "job_title": {"type": "string"},
            },
            "required": ["contact_id"],
        },
    },
    {
        "name": "freshsales_list_deals",
        "description": "List deals in Freshsales CRM with optional filtering and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
                "stage_id": {"type": "integer", "description": "Filter by deal stage ID"},
                "sort": {"type": "string", "description": "Sort field, e.g. 'amount'"},
            },
        },
    },
    {
        "name": "freshsales_create_deal",
        "description": "Create a new deal in Freshsales CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Deal name"},
                "amount": {"type": "number", "description": "Deal value/amount"},
                "sales_account_id": {"type": "integer", "description": "Associated account ID"},
                "contact_id": {"type": "integer", "description": "Associated contact ID"},
                "deal_stage_id": {"type": "integer", "description": "Pipeline stage ID"},
                "expected_close": {"type": "string", "description": "Expected close date (YYYY-MM-DD)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "freshsales_list_accounts",
        "description": "List sales accounts (companies) in Freshsales CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query"},
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
                "sort": {"type": "string"},
            },
        },
    },
]


def _base_url() -> str:
    domain = os.getenv("FRESHSALES_DOMAIN", "")
    return f"https://{domain}.freshsales.io/api"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Token token={api_key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("FRESHSALES_API_KEY", "")
    if not api_key:
        return {"error": "FRESHSALES_API_KEY not configured"}

    domain = os.getenv("FRESHSALES_DOMAIN", "")
    if not domain:
        return {"error": "FRESHSALES_DOMAIN not configured"}

    base_url = f"https://{domain}.freshsales.io/api"

    try:
        async with httpx.AsyncClient(
            base_url=base_url, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "freshsales_list_contacts":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                    "sort": arguments.get("sort", "created_at"),
                    "sort_type": arguments.get("sort_type", "desc"),
                }
                if q := arguments.get("q"):
                    params["q"] = q
                r = await c.get("/contacts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshsales_create_contact":
                body: dict[str, Any] = {"contact": {"email": arguments["email"]}}
                for k in ("first_name", "last_name", "phone", "mobile", "job_title", "lead_source_id"):
                    if k in arguments:
                        body["contact"][k] = arguments[k]
                r = await c.post("/contacts", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshsales_update_contact":
                cid = arguments["contact_id"]
                body = {"contact": {
                    k: arguments[k]
                    for k in ("first_name", "last_name", "email", "phone", "job_title")
                    if k in arguments
                }}
                r = await c.put(f"/contacts/{cid}", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshsales_list_deals":
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                if "stage_id" in arguments:
                    params["deal_stage_id"] = arguments["stage_id"]
                if "sort" in arguments:
                    params["sort"] = arguments["sort"]
                r = await c.get("/deals", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshsales_create_deal":
                body = {"deal": {"name": arguments["name"]}}
                for k in ("amount", "sales_account_id", "contact_id", "deal_stage_id", "expected_close"):
                    if k in arguments:
                        body["deal"][k] = arguments[k]
                r = await c.post("/deals", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshsales_list_accounts":
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                if q := arguments.get("q"):
                    params["q"] = q
                if "sort" in arguments:
                    params["sort"] = arguments["sort"]
                r = await c.get("/sales_accounts", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("freshsales_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
