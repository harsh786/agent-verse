"""Zoho CRM MCP server — records CRUD and search across CRM modules.

Environment variables:
  ZOHO_ACCESS_TOKEN: OAuth2 access token
  ZOHO_DOMAIN: zoho.com | zoho.eu | zoho.in | zoho.com.au (default: zoho.com)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "zoho_list_records",
        "description": "List records in a Zoho CRM module (Leads, Contacts, Accounts, Deals, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": "Module API name, e.g. Leads, Contacts, Accounts, Deals",
                },
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 20},
                "sort_by": {"type": "string"},
                "sort_order": {"type": "string", "enum": ["asc", "desc"]},
            },
            "required": ["module"],
        },
    },
    {
        "name": "zoho_create_record",
        "description": "Create a new record in a Zoho CRM module",
        "parameters": {
            "type": "object",
            "properties": {
                "module": {"type": "string"},
                "data": {
                    "type": "object",
                    "description": "Record field values as key-value pairs",
                },
            },
            "required": ["module", "data"],
        },
    },
    {
        "name": "zoho_update_record",
        "description": "Update an existing Zoho CRM record by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "module": {"type": "string"},
                "record_id": {"type": "string"},
                "data": {"type": "object", "description": "Fields to update"},
            },
            "required": ["module", "record_id", "data"],
        },
    },
    {
        "name": "zoho_delete_record",
        "description": "Delete a Zoho CRM record by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "module": {"type": "string"},
                "record_id": {"type": "string"},
            },
            "required": ["module", "record_id"],
        },
    },
    {
        "name": "zoho_search_records",
        "description": "Search Zoho CRM records using criteria or full-text search",
        "parameters": {
            "type": "object",
            "properties": {
                "module": {"type": "string"},
                "criteria": {
                    "type": "string",
                    "description": "Search criteria, e.g. (Last_Name:equals:Smith)",
                },
                "word": {
                    "type": "string",
                    "description": "Full-text search word",
                },
                "email": {"type": "string", "description": "Search by email"},
                "phone": {"type": "string", "description": "Search by phone"},
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 20},
            },
            "required": ["module"],
        },
    },
]


def _base_url() -> str:
    domain = os.getenv("ZOHO_DOMAIN", "zoho.com")
    return f"https://www.zohoapis.{domain}/crm/v3"


def _headers() -> dict[str, str]:
    token = os.getenv("ZOHO_ACCESS_TOKEN", "")
    return {"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("ZOHO_ACCESS_TOKEN", "")
    if not token:
        return {"error": "ZOHO_ACCESS_TOKEN not configured"}

    base = _base_url()

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=_headers()) as c:
            if tool_name == "zoho_list_records":
                module = arguments["module"]
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 20),
                }
                if sb := arguments.get("sort_by"):
                    params["sort_by"] = sb
                if so := arguments.get("sort_order"):
                    params["sort_order"] = so
                r = await c.get(f"{base}/{module}", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_create_record":
                module = arguments["module"]
                payload = {"data": [arguments["data"]]}
                r = await c.post(f"{base}/{module}", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_update_record":
                module, rid = arguments["module"], arguments["record_id"]
                payload = {"data": [{**arguments["data"], "id": rid}]}
                r = await c.put(f"{base}/{module}", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_delete_record":
                module, rid = arguments["module"], arguments["record_id"]
                r = await c.delete(f"{base}/{module}/{rid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_search_records":
                module = arguments["module"]
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 20),
                }
                for key in ("criteria", "word", "email", "phone"):
                    if val := arguments.get(key):
                        params[key] = val
                r = await c.get(f"{base}/{module}/search", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("zoho_crm_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
