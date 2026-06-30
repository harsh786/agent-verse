"""Squarespace MCP server — Squarespace website pages, products, orders, and inventory.

Environment:
  SQUARESPACE_API_KEY: Squarespace API key from Settings > Advanced > Developer API Keys
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.squarespace.com/1.0"

TOOL_DEFINITIONS = [
    {
        "name": "squarespace_list_pages",
        "description": "List all pages on the Squarespace website",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Page type filter: page, folder, index, blog, gallery"},
            },
        },
    },
    {
        "name": "squarespace_list_products",
        "description": "List products in the Squarespace commerce store",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Product type: PHYSICAL, DIGITAL, SERVICE, GIFT_CARD"},
                "cursor": {"type": "string", "description": "Pagination cursor from previous response"},
            },
        },
    },
    {
        "name": "squarespace_list_orders",
        "description": "List commerce orders in Squarespace",
        "parameters": {
            "type": "object",
            "properties": {
                "fulfillment_status": {"type": "string", "description": "Filter by fulfillment: PENDING, FULFILLED, CANCELED"},
                "modified_after": {"type": "string", "description": "ISO 8601 datetime: only orders modified after this date"},
                "cursor": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "squarespace_get_order",
        "description": "Get details of a specific Squarespace order by order ID",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Squarespace order ID"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "squarespace_list_inventory",
        "description": "List product inventory levels for the Squarespace store",
        "parameters": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string", "description": "Pagination cursor from previous response"},
            },
        },
    },
    {
        "name": "squarespace_get_store_info",
        "description": "Get store profile and configuration information from Squarespace",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("SQUARESPACE_API_KEY", "")
    if not api_key:
        return {"error": "SQUARESPACE_API_KEY not configured"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "AgentVerse-MCP/1.0",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "squarespace_list_pages":
                r = await client.get(f"{BASE}/pages", headers=headers)
                r.raise_for_status()
                data = r.json()
                pages = data.get("pages", data.get("entries", []))
                if "type" in arguments:
                    pages = [p for p in pages if p.get("type") == arguments["type"]]
                return {"pages": pages}

            elif tool_name == "squarespace_list_products":
                params: dict[str, Any] = {}
                if "type" in arguments:
                    params["type"] = arguments["type"]
                if "cursor" in arguments:
                    params["cursor"] = arguments["cursor"]
                r = await client.get(f"{BASE}/commerce/products", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "products": [
                        {
                            "id": p.get("id"),
                            "name": p.get("name"),
                            "type": p.get("type"),
                            "variants": len(p.get("variants", [])),
                            "is_visible": p.get("isVisible"),
                        }
                        for p in data.get("products", [])
                    ],
                    "next_cursor": data.get("pagination", {}).get("nextPageCursor"),
                    "has_next_page": data.get("pagination", {}).get("hasNextPage", False),
                }

            elif tool_name == "squarespace_list_orders":
                params = {}
                if "fulfillment_status" in arguments:
                    params["fulfillmentStatus"] = arguments["fulfillment_status"]
                if "modified_after" in arguments:
                    params["modifiedAfter"] = arguments["modified_after"]
                if "cursor" in arguments:
                    params["cursor"] = arguments["cursor"]
                r = await client.get(f"{BASE}/commerce/orders", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "orders": [
                        {
                            "id": o.get("id"),
                            "order_number": o.get("orderNumber"),
                            "fulfillment_status": o.get("fulfillmentStatus"),
                            "grand_total": o.get("grandTotal"),
                            "created_on": o.get("createdOn"),
                            "customer_email": o.get("customerEmail"),
                        }
                        for o in data.get("result", [])
                    ],
                    "next_cursor": data.get("pagination", {}).get("nextPageCursor"),
                }

            elif tool_name == "squarespace_get_order":
                r = await client.get(
                    f"{BASE}/commerce/orders/{arguments['order_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "squarespace_list_inventory":
                params = {}
                if "cursor" in arguments:
                    params["cursor"] = arguments["cursor"]
                r = await client.get(f"{BASE}/commerce/inventory", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "inventory": data.get("inventory", []),
                    "next_cursor": data.get("pagination", {}).get("nextPageCursor"),
                }

            elif tool_name == "squarespace_get_store_info":
                r = await client.get(f"{BASE}/profiles/me", headers=headers)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("squarespace_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
