"""Unbounce MCP server — landing pages, lead capture, and conversion analytics.

Environment:
  UNBOUNCE_API_KEY: Unbounce API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.unbounce.com"

TOOL_DEFINITIONS = [
    {
        "name": "unbounce_list_pages",
        "description": "List landing pages in the Unbounce account with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Filter by specific page ID"},
                "state": {"type": "string", "description": "Filter by state: published, unpublished"},
                "client_id": {"type": "string", "description": "Filter by client/sub-account ID"},
                "offset": {"type": "integer", "description": "Pagination offset"},
                "count": {"type": "integer", "description": "Maximum pages to return"},
            },
        },
    },
    {
        "name": "unbounce_list_leads",
        "description": "List form submission leads captured on Unbounce pages",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Filter leads by page ID"},
                "from_date": {"type": "string", "description": "Start date filter YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date filter YYYY-MM-DD"},
                "offset": {"type": "integer", "description": "Pagination offset"},
                "count": {"type": "integer", "description": "Maximum leads"},
            },
        },
    },
    {
        "name": "unbounce_get_lead",
        "description": "Get details of a specific Unbounce lead by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string", "description": "Unbounce lead ID"},
            },
            "required": ["lead_id"],
        },
    },
    {
        "name": "unbounce_list_clients",
        "description": "List client sub-accounts in the Unbounce account",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "description": "Pagination offset"},
                "count": {"type": "integer", "description": "Maximum clients"},
            },
        },
    },
    {
        "name": "unbounce_get_page_stats",
        "description": "Get conversion statistics for a specific Unbounce landing page",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Unbounce page ID"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "unbounce_create_page_form",
        "description": "Get form information and fields for an Unbounce page",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Unbounce page ID to get form for"},
            },
            "required": ["page_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("UNBOUNCE_API_KEY", "")
    if not api_key:
        return {"error": "UNBOUNCE_API_KEY not configured"}

    headers = {"Authorization": f"Basic {api_key}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "unbounce_list_pages":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/pages", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "unbounce_list_leads":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/leads", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "unbounce_get_lead":
                r = await client.get(
                    f"{BASE_URL}/leads/{arguments['lead_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "unbounce_list_clients":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/clients", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "unbounce_get_page_stats":
                page_id = arguments["page_id"]
                params: dict[str, Any] = {}
                if "from_date" in arguments:
                    params["from"] = arguments["from_date"]
                if "to_date" in arguments:
                    params["to"] = arguments["to_date"]
                r = await client.get(
                    f"{BASE_URL}/pages/{page_id}/page_group_stats",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "unbounce_create_page_form":
                r = await client.get(
                    f"{BASE_URL}/pages/{arguments['page_id']}/form_fields",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
