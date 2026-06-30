"""Digistore24 MCP server — digital marketplace orders, products, and commissions.

Environment:
  DIGISTORE24_API_KEY: Digistore24 API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://www.digistore24.com/api/call"

TOOL_DEFINITIONS = [
    {
        "name": "digistore24_list_orders",
        "description": "List orders from Digistore24 with optional date and status filters",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "to_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Orders per page"},
                "status": {"type": "string", "description": "Filter by order status"},
            },
        },
    },
    {
        "name": "digistore24_get_order",
        "description": "Get detailed information about a specific Digistore24 order",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Digistore24 order ID"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "digistore24_list_products",
        "description": "List products available in the Digistore24 marketplace",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Products per page"},
            },
        },
    },
    {
        "name": "digistore24_create_affiliate_link",
        "description": "Generate an affiliate tracking link for a Digistore24 product",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product ID to create affiliate link for"},
                "affiliate_id": {"type": "string", "description": "Your affiliate ID"},
                "campaign": {"type": "string", "description": "Optional campaign tracking parameter"},
            },
            "required": ["product_id", "affiliate_id"],
        },
    },
    {
        "name": "digistore24_get_commission_stats",
        "description": "Get affiliate commission statistics for a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "digistore24_list_subscriptions",
        "description": "List active subscriptions and recurring billing details",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Subscriptions per page"},
                "status": {"type": "string", "description": "Filter by status: active, cancelled"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("DIGISTORE24_API_KEY", "")
    if not api_key:
        return {"error": "DIGISTORE24_API_KEY not configured"}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            def _url(method: str) -> str:
                return f"{BASE_URL}/{api_key}/json/{method}"

            if tool_name == "digistore24_list_orders":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(_url("listOrdersLog"), params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "digistore24_get_order":
                r = await client.get(_url("getOrder"), params={"order_id": arguments["order_id"]})
                r.raise_for_status()
                return r.json()

            if tool_name == "digistore24_list_products":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(_url("listProducts"), params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "digistore24_create_affiliate_link":
                r = await client.get(
                    _url("createAffiliateLink"),
                    params={
                        "product_id": arguments["product_id"],
                        "affiliate_id": arguments["affiliate_id"],
                        "campaign": arguments.get("campaign", ""),
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "digistore24_get_commission_stats":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(_url("getAffiliateStats"), params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "digistore24_list_subscriptions":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(_url("listSubscriptions"), params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
