"""SugarCRM MCP server — accounts, contacts, and leads management.

Environment variables:
  SUGARCRM_ACCESS_TOKEN: SugarCRM OAuth 2.0 access token
  SUGARCRM_INSTANCE_URL: SugarCRM instance URL, e.g. https://mycompany.sugarcrm.com
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "sugarcrm_list_accounts",
        "description": "List accounts (companies/organisations) in SugarCRM with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
                "limit": {"type": "integer", "default": 25},
                "order_by": {"type": "string", "description": "Field to sort by, e.g. 'name:ASC'"},
                "q": {"type": "string", "description": "Full-text search query"},
                "fields": {"type": "string", "description": "Comma-separated fields to return"},
            },
        },
    },
    {
        "name": "sugarcrm_create_account",
        "description": "Create a new account (company) in SugarCRM",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Account name"},
                "phone_office": {"type": "string"},
                "website": {"type": "string"},
                "industry": {"type": "string"},
                "employees": {"type": "integer"},
                "billing_address_city": {"type": "string"},
                "billing_address_country": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "sugarcrm_list_contacts",
        "description": "List contacts in SugarCRM with optional filtering and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 25},
                "order_by": {"type": "string"},
                "q": {"type": "string", "description": "Search query"},
                "fields": {"type": "string"},
            },
        },
    },
    {
        "name": "sugarcrm_create_contact",
        "description": "Create a new contact in SugarCRM",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "email1": {"type": "string", "description": "Primary email address"},
                "phone_work": {"type": "string"},
                "title": {"type": "string"},
                "account_id": {"type": "string", "description": "Associated account ID"},
            },
            "required": ["last_name"],
        },
    },
    {
        "name": "sugarcrm_list_leads",
        "description": "List leads in SugarCRM",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 25},
                "order_by": {"type": "string"},
                "q": {"type": "string"},
                "status": {"type": "string", "description": "Filter by lead status"},
            },
        },
    },
    {
        "name": "sugarcrm_create_lead",
        "description": "Create a new lead in SugarCRM",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "email1": {"type": "string"},
                "phone_work": {"type": "string"},
                "company": {"type": "string"},
                "title": {"type": "string"},
                "lead_source": {"type": "string", "description": "Lead source, e.g. 'Web Site', 'Cold Call'"},
            },
            "required": ["last_name"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {
        "oauth-token": token,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("SUGARCRM_ACCESS_TOKEN", "")
    if not token:
        return {"error": "SUGARCRM_ACCESS_TOKEN not configured"}

    instance_url = os.getenv("SUGARCRM_INSTANCE_URL", "").rstrip("/")
    if not instance_url:
        return {"error": "SUGARCRM_INSTANCE_URL not configured"}

    base_url = f"{instance_url}/rest/v11_1"

    try:
        async with httpx.AsyncClient(
            base_url=base_url, headers=_headers(token), timeout=30.0
        ) as c:
            if tool_name == "sugarcrm_list_accounts":
                params: dict[str, Any] = {
                    "offset": arguments.get("offset", 0),
                    "max_num": arguments.get("limit", 25),
                }
                if q := arguments.get("q"):
                    params["q"] = q
                if "order_by" in arguments:
                    params["order_by"] = arguments["order_by"]
                if "fields" in arguments:
                    params["fields"] = arguments["fields"]
                r = await c.get("/Accounts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "sugarcrm_create_account":
                body: dict[str, Any] = {"name": arguments["name"]}
                for k in ("phone_office", "website", "industry", "employees",
                          "billing_address_city", "billing_address_country"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/Accounts", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "sugarcrm_list_contacts":
                params = {
                    "offset": arguments.get("offset", 0),
                    "max_num": arguments.get("limit", 25),
                }
                if q := arguments.get("q"):
                    params["q"] = q
                if "order_by" in arguments:
                    params["order_by"] = arguments["order_by"]
                r = await c.get("/Contacts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "sugarcrm_create_contact":
                body = {"last_name": arguments["last_name"]}
                for k in ("first_name", "email1", "phone_work", "title", "account_id"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/Contacts", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "sugarcrm_list_leads":
                params = {
                    "offset": arguments.get("offset", 0),
                    "max_num": arguments.get("limit", 25),
                }
                if q := arguments.get("q"):
                    params["q"] = q
                if "order_by" in arguments:
                    params["order_by"] = arguments["order_by"]
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await c.get("/Leads", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "sugarcrm_create_lead":
                body = {"last_name": arguments["last_name"]}
                for k in ("first_name", "email1", "phone_work", "company", "title", "lead_source"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/Leads", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("sugarcrm_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
