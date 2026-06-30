"""ThriveCart MCP server — checkout platform, products, orders, and affiliates.

Environment:
  THRIVECART_API_KEY: ThriveCart API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://thrivecart.com/api/external/v1"

TOOL_DEFINITIONS = [
    {
        "name": "thrivecart_list_products",
        "description": "List all products configured in the ThriveCart account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "limit": {"type": "integer", "description": "Products per page"},
            },
        },
    },
    {
        "name": "thrivecart_list_orders",
        "description": "List orders processed through ThriveCart with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Filter by product ID"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
                "page": {"type": "integer", "description": "Page number"},
                "limit": {"type": "integer", "description": "Orders per page"},
            },
        },
    },
    {
        "name": "thrivecart_get_order",
        "description": "Get details of a specific ThriveCart order",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "ThriveCart order ID"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "thrivecart_list_customers",
        "description": "List customers who have purchased through ThriveCart",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "limit": {"type": "integer", "description": "Customers per page"},
                "email": {"type": "string", "description": "Search by customer email"},
            },
        },
    },
    {
        "name": "thrivecart_create_coupon",
        "description": "Create a discount coupon for a ThriveCart product",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product to create coupon for"},
                "code": {"type": "string", "description": "Coupon code string"},
                "discount_type": {"type": "string", "description": "Discount type: flat or percent"},
                "discount_amount": {"type": "number", "description": "Discount value"},
                "limit_uses": {"type": "integer", "description": "Maximum number of uses"},
            },
            "required": ["product_id", "code", "discount_type", "discount_amount"],
        },
    },
    {
        "name": "thrivecart_get_revenue_stats",
        "description": "Get revenue and sales statistics for the ThriveCart account",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD"},
                "product_id": {"type": "string", "description": "Filter by product"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("THRIVECART_API_KEY", "")
    if not api_key:
        return {"error": "THRIVECART_API_KEY not configured"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "thrivecart_list_products":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/products", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "thrivecart_list_orders":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/orders", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "thrivecart_get_order":
                r = await client.get(
                    f"{BASE_URL}/orders/{arguments['order_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "thrivecart_list_customers":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/customers", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "thrivecart_create_coupon":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(
                    f"{BASE_URL}/products/{arguments['product_id']}/coupons",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "thrivecart_get_revenue_stats":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/stats/revenue", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
