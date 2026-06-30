"""Koala intent data MCP server — visitor tracking, account signals, and firmographics.

Environment:
  KOALA_API_KEY: Koala API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.getkoala.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "koala_list_visitors",
        "description": "List website visitors tracked by Koala with intent signals",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Visitors per page"},
                "from_date": {"type": "string", "description": "Start date filter YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date filter YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "koala_get_account_signals",
        "description": "Get intent signals and buying signals for a specific account/company",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Company domain to get signals for"},
            },
            "required": ["domain"],
        },
    },
    {
        "name": "koala_search_accounts",
        "description": "Search for accounts in Koala by domain or company name",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (domain or company name)"},
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "koala_list_page_views",
        "description": "List page views from identified visitors on the website",
        "parameters": {
            "type": "object",
            "properties": {
                "account_domain": {"type": "string", "description": "Filter by company domain"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "koala_get_firmographics",
        "description": "Get firmographic data (industry, size, revenue) for a company domain",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Company domain to get firmographics for"},
            },
            "required": ["domain"],
        },
    },
    {
        "name": "koala_track_event",
        "description": "Track a custom event in Koala for intent scoring",
        "parameters": {
            "type": "object",
            "properties": {
                "event_name": {"type": "string", "description": "Name of the event to track"},
                "visitor_id": {"type": "string", "description": "Koala visitor ID"},
                "properties": {"type": "object", "description": "Event properties"},
            },
            "required": ["event_name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("KOALA_API_KEY", "")
    if not api_key:
        return {"error": "KOALA_API_KEY not configured"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "koala_list_visitors":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/visitors", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "koala_get_account_signals":
                r = await client.get(
                    f"{BASE_URL}/accounts/{arguments['domain']}/signals",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "koala_search_accounts":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/accounts/search", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "koala_list_page_views":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/page_views", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "koala_get_firmographics":
                r = await client.get(
                    f"{BASE_URL}/accounts/{arguments['domain']}/firmographics",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "koala_track_event":
                r = await client.post(
                    f"{BASE_URL}/events",
                    headers=headers,
                    json={
                        "event": arguments["event_name"],
                        "visitor_id": arguments.get("visitor_id"),
                        "properties": arguments.get("properties", {}),
                    },
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
