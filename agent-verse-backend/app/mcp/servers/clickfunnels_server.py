"""ClickFunnels MCP server — sales funnel management, contacts, and orders.

Environment:
  CLICKFUNNELS_API_KEY: ClickFunnels API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://app.clickfunnels.com/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "clickfunnels_list_funnels",
        "description": "List all sales funnels in the ClickFunnels account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number for pagination"},
                "per_page": {"type": "integer", "description": "Funnels per page"},
            },
        },
    },
    {
        "name": "clickfunnels_list_contacts",
        "description": "List contacts (leads) in ClickFunnels with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Search by contact email"},
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Contacts per page"},
            },
        },
    },
    {
        "name": "clickfunnels_create_contact",
        "description": "Create a new contact (lead) in ClickFunnels",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address"},
                "first_name": {"type": "string", "description": "First name"},
                "last_name": {"type": "string", "description": "Last name"},
                "phone": {"type": "string", "description": "Phone number"},
                "funnel_id": {"type": "integer", "description": "Associate with a specific funnel"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "clickfunnels_list_purchases",
        "description": "List purchases and revenue from ClickFunnels orders",
        "parameters": {
            "type": "object",
            "properties": {
                "funnel_id": {"type": "integer", "description": "Filter by funnel ID"},
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Purchases per page"},
            },
        },
    },
    {
        "name": "clickfunnels_list_orders",
        "description": "List orders placed through ClickFunnels funnels",
        "parameters": {
            "type": "object",
            "properties": {
                "funnel_id": {"type": "integer", "description": "Filter by funnel"},
                "status": {"type": "string", "description": "Filter by status: paid, pending, refunded"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "clickfunnels_get_funnel_stats",
        "description": "Get conversion stats and analytics for a ClickFunnels funnel",
        "parameters": {
            "type": "object",
            "properties": {
                "funnel_id": {"type": "integer", "description": "Funnel ID to get stats for"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["funnel_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("CLICKFUNNELS_API_KEY", "")
    if not api_key:
        return {"error": "CLICKFUNNELS_API_KEY not configured"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "clickfunnels_list_funnels":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/workspaces/1/funnels", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "clickfunnels_list_contacts":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/workspaces/1/contacts", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "clickfunnels_create_contact":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(
                    f"{BASE_URL}/workspaces/1/contacts",
                    headers=headers,
                    json={"contact": payload},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "clickfunnels_list_purchases":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/workspaces/1/purchases", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "clickfunnels_list_orders":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/workspaces/1/orders", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "clickfunnels_get_funnel_stats":
                funnel_id = arguments["funnel_id"]
                params = {k: v for k, v in arguments.items() if k != "funnel_id" and v is not None}
                r = await client.get(
                    f"{BASE_URL}/workspaces/1/funnels/{funnel_id}/statistics",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
