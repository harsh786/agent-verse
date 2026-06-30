"""Ecwid MCP server — Ecwid e-commerce store products, orders, and statistics.

Environment:
  ECWID_SECRET_TOKEN: Ecwid store secret API token
  ECWID_STORE_ID: Ecwid numeric store ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "ecwid_list_products",
        "description": "List products in an Ecwid store with optional search and filters",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Search keyword for product name/description"},
                "enabled": {"type": "boolean", "description": "Filter by enabled/disabled status"},
                "in_stock": {"type": "boolean", "description": "Filter to show only in-stock products"},
                "limit": {"type": "integer", "description": "Number of products to return (max 100)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "ecwid_create_product",
        "description": "Create a new product in the Ecwid store",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Product name"},
                "price": {"type": "number", "description": "Product price"},
                "description": {"type": "string", "description": "Product HTML description"},
                "sku": {"type": "string", "description": "Product SKU"},
                "quantity": {"type": "integer", "description": "Stock quantity"},
                "enabled": {"type": "boolean", "description": "Whether the product is enabled/visible", "default": True},
            },
            "required": ["name", "price"],
        },
    },
    {
        "name": "ecwid_update_product",
        "description": "Update an existing Ecwid product",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer", "description": "Ecwid product ID"},
                "name": {"type": "string", "description": "Updated product name"},
                "price": {"type": "number", "description": "Updated price"},
                "quantity": {"type": "integer", "description": "Updated stock quantity"},
                "enabled": {"type": "boolean", "description": "Enable or disable the product"},
                "description": {"type": "string", "description": "Updated HTML description"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "ecwid_list_orders",
        "description": "List orders in an Ecwid store",
        "parameters": {
            "type": "object",
            "properties": {
                "payment_status": {"type": "string", "description": "Filter by payment status: AWAITING_PAYMENT, PAID, CANCELLED, REFUNDED"},
                "fulfillment_status": {"type": "string", "description": "Filter by fulfillment: AWAITING_PROCESSING, PROCESSING, SHIPPED, DELIVERED, RETURNED"},
                "limit": {"type": "integer", "description": "Number of orders to return (max 100)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "ecwid_update_order_status",
        "description": "Update the payment or fulfillment status of an Ecwid order",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Ecwid order ID (orderNumber)"},
                "payment_status": {"type": "string", "description": "New payment status: AWAITING_PAYMENT, PAID, CANCELLED, REFUNDED"},
                "fulfillment_status": {"type": "string", "description": "New fulfillment status: AWAITING_PROCESSING, PROCESSING, SHIPPED, DELIVERED"},
                "tracking_number": {"type": "string", "description": "Shipping tracking number"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "ecwid_get_store_stats",
        "description": "Get store profile and overview statistics for an Ecwid store",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


def _base() -> str:
    store_id = os.getenv("ECWID_STORE_ID", "")
    return f"https://app.ecwid.com/api/v3/{store_id}"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.getenv('ECWID_SECRET_TOKEN', '')}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    secret_token = os.getenv("ECWID_SECRET_TOKEN", "")
    store_id = os.getenv("ECWID_STORE_ID", "")
    if not secret_token:
        return {"error": "ECWID_SECRET_TOKEN not configured"}
    if not store_id:
        return {"error": "ECWID_STORE_ID not configured"}

    base = f"https://app.ecwid.com/api/v3/{store_id}"
    headers = {
        "Authorization": f"Bearer {secret_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "ecwid_list_products":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "keyword" in arguments:
                    params["keyword"] = arguments["keyword"]
                if "enabled" in arguments:
                    params["enabled"] = str(arguments["enabled"]).lower()
                if "in_stock" in arguments:
                    params["inStock"] = str(arguments["in_stock"]).lower()
                r = await client.get(f"{base}/products", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "products": [
                        {
                            "id": p.get("id"),
                            "name": p.get("name"),
                            "price": p.get("price"),
                            "sku": p.get("sku"),
                            "quantity": p.get("quantity"),
                            "enabled": p.get("enabled"),
                        }
                        for p in data.get("items", [])
                    ],
                    "total": data.get("total", 0),
                    "count": data.get("count", 0),
                }

            elif tool_name == "ecwid_create_product":
                payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "price": arguments["price"],
                    "enabled": arguments.get("enabled", True),
                }
                for field in ("description", "sku", "quantity"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.post(f"{base}/products", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                return {"id": data.get("id")}

            elif tool_name == "ecwid_update_product":
                payload = {}
                for field in ("name", "price", "quantity", "enabled", "description"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.put(
                    f"{base}/products/{arguments['product_id']}",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return {"updated": True, "product_id": arguments["product_id"]}

            elif tool_name == "ecwid_list_orders":
                params = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "payment_status" in arguments:
                    params["paymentStatus"] = arguments["payment_status"]
                if "fulfillment_status" in arguments:
                    params["fulfillmentStatus"] = arguments["fulfillment_status"]
                r = await client.get(f"{base}/orders", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "orders": [
                        {
                            "id": o.get("id"),
                            "order_number": o.get("orderNumber"),
                            "total": o.get("total"),
                            "payment_status": o.get("paymentStatus"),
                            "fulfillment_status": o.get("fulfillmentStatus"),
                            "email": o.get("email"),
                        }
                        for o in data.get("items", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "ecwid_update_order_status":
                payload = {}
                if "payment_status" in arguments:
                    payload["paymentStatus"] = arguments["payment_status"]
                if "fulfillment_status" in arguments:
                    payload["fulfillmentStatus"] = arguments["fulfillment_status"]
                if "tracking_number" in arguments:
                    payload["trackingNumber"] = arguments["tracking_number"]
                r = await client.put(
                    f"{base}/orders/{arguments['order_id']}",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return {"updated": True, "order_id": arguments["order_id"]}

            elif tool_name == "ecwid_get_store_stats":
                r = await client.get(f"{base}/profile", headers=headers)
                r.raise_for_status()
                data = r.json()
                return {
                    "store_id": data.get("storeId"),
                    "store_name": data.get("storeName"),
                    "company_name": data.get("company", {}).get("companyName"),
                    "currency": data.get("defaultProductSortOrder"),
                    "order_count": data.get("orderCount"),
                    "product_count": data.get("productCount"),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("ecwid_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
