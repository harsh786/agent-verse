"""SamCart MCP server — checkout platform products, orders, and customers.

Environment:
  SAMCART_API_KEY: SamCart API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.samcart.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "samcart_list_products",
        "description": "List all products in SamCart",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "samcart_get_product",
        "description": "Get a specific SamCart product by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "samcart_list_orders",
        "description": "List orders in SamCart",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer", "description": "Filter by product"},
                "customer_id": {"type": "integer", "description": "Filter by customer"},
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "samcart_get_order",
        "description": "Get a specific SamCart order by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "samcart_list_customers",
        "description": "List customers in SamCart",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "samcart_get_customer_subscriptions",
        "description": "Get all subscriptions for a specific customer",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer"},
            },
            "required": ["customer_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("SAMCART_API_KEY", "")
    if not api_key:
        return {"error": "SAMCART_API_KEY not configured"}

    headers = {
        "sc-api": api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "samcart_list_products":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                r = await c.get(f"{BASE}/products", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "samcart_get_product":
                pid = arguments["product_id"]
                r = await c.get(f"{BASE}/products/{pid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "samcart_list_orders":
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                if pid := arguments.get("product_id"):
                    params["product_id"] = pid
                if cid := arguments.get("customer_id"):
                    params["customer_id"] = cid
                r = await c.get(f"{BASE}/orders", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "samcart_get_order":
                oid = arguments["order_id"]
                r = await c.get(f"{BASE}/orders/{oid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "samcart_list_customers":
                params = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 25),
                }
                if email := arguments.get("email"):
                    params["email"] = email
                r = await c.get(f"{BASE}/customers", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "samcart_get_customer_subscriptions":
                cid = arguments["customer_id"]
                r = await c.get(f"{BASE}/customers/{cid}/subscriptions")
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("samcart_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
