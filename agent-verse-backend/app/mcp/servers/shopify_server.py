"""Shopify MCP server — products, orders, customers, inventory management.

Environment:
  SHOPIFY_STORE_URL:     Store domain, e.g. 'mystore.myshopify.com'
  SHOPIFY_ACCESS_TOKEN:  Admin API access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SHOPIFY_API_VERSION = "2024-01"


def _base() -> str:
    store = os.getenv("SHOPIFY_STORE_URL", "").strip("/")
    return f"https://{store}/admin/api/{SHOPIFY_API_VERSION}"


def _headers() -> dict[str, str]:
    token = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
    return {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "shopify_list_products",
        "description": "List Shopify products with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "page_info": {"type": "string", "description": "Pagination cursor"},
                "status": {"type": "string", "enum": ["active", "archived", "draft"]},
                "vendor": {"type": "string"},
                "product_type": {"type": "string"},
            },
        },
    },
    {
        "name": "shopify_get_product",
        "description": "Get a single Shopify product by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "shopify_create_product",
        "description": "Create a new Shopify product",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body_html": {"type": "string", "description": "Product description in HTML"},
                "vendor": {"type": "string"},
                "product_type": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "draft", "archived"], "default": "draft"},
                "tags": {"type": "string", "description": "Comma-separated tags"},
                "variants": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Product variants with price, sku, etc.",
                },
                "images": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Product images with src or attachment",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "shopify_update_product",
        "description": "Update an existing Shopify product",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
                "title": {"type": "string"},
                "body_html": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "draft", "archived"]},
                "tags": {"type": "string"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "shopify_list_orders",
        "description": "List Shopify orders with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "status": {
                    "type": "string",
                    "enum": ["open", "closed", "cancelled", "any"],
                    "default": "open",
                },
                "financial_status": {
                    "type": "string",
                    "enum": ["authorized", "pending", "paid", "refunded", "voided", "any"],
                },
                "fulfillment_status": {
                    "type": "string",
                    "enum": ["fulfilled", "unfulfilled", "partial", "restocked", "any"],
                },
                "created_at_min": {"type": "string", "description": "ISO 8601 datetime"},
                "created_at_max": {"type": "string", "description": "ISO 8601 datetime"},
            },
        },
    },
    {
        "name": "shopify_get_order",
        "description": "Get a single Shopify order by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "shopify_list_customers",
        "description": "List Shopify customers",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "since_id": {"type": "integer"},
                "created_at_min": {"type": "string"},
                "created_at_max": {"type": "string"},
                "query": {"type": "string", "description": "Search query"},
            },
        },
    },
    {
        "name": "shopify_create_customer",
        "description": "Create a new Shopify customer",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "tags": {"type": "string"},
                "note": {"type": "string"},
                "accepts_marketing": {"type": "boolean", "default": False},
                "addresses": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["email"],
        },
    },
    {
        "name": "shopify_list_collections",
        "description": "List Shopify custom collections",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "product_id": {"type": "integer", "description": "Filter by product"},
            },
        },
    },
    {
        "name": "shopify_update_inventory",
        "description": "Set inventory level for a product variant at a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location_id": {"type": "integer"},
                "inventory_item_id": {"type": "integer"},
                "available": {"type": "integer", "description": "Quantity available"},
            },
            "required": ["location_id", "inventory_item_id", "available"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    store = os.getenv("SHOPIFY_STORE_URL", "")
    token = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
    if not store or not token:
        return {"error": "SHOPIFY_STORE_URL and SHOPIFY_ACCESS_TOKEN must be configured"}

    base = _base()

    try:
        async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as c:
            if tool_name == "shopify_list_products":
                params: dict[str, Any] = {"limit": arguments.get("limit", 50)}
                for key in ["page_info", "status", "vendor", "product_type"]:
                    if v := arguments.get(key):
                        params[key] = v
                r = await c.get(f"{base}/products.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "shopify_get_product":
                r = await c.get(f"{base}/products/{arguments['product_id']}.json")
                r.raise_for_status()
                return r.json()

            elif tool_name == "shopify_create_product":
                product: dict[str, Any] = {"title": arguments["title"]}
                for key in ["body_html", "vendor", "product_type", "status", "tags", "variants", "images"]:
                    if v := arguments.get(key):
                        product[key] = v
                r = await c.post(f"{base}/products.json", json={"product": product})
                r.raise_for_status()
                return r.json()

            elif tool_name == "shopify_update_product":
                pid = arguments["product_id"]
                product = {}
                for key in ["title", "body_html", "status", "tags"]:
                    if v := arguments.get(key):
                        product[key] = v
                r = await c.put(f"{base}/products/{pid}.json", json={"product": product})
                r.raise_for_status()
                return r.json()

            elif tool_name == "shopify_list_orders":
                params = {"limit": arguments.get("limit", 50)}
                for key in ["status", "financial_status", "fulfillment_status", "created_at_min", "created_at_max"]:
                    if v := arguments.get(key):
                        params[key] = v
                r = await c.get(f"{base}/orders.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "shopify_get_order":
                r = await c.get(f"{base}/orders/{arguments['order_id']}.json")
                r.raise_for_status()
                return r.json()

            elif tool_name == "shopify_list_customers":
                params = {"limit": arguments.get("limit", 50)}
                for key in ["since_id", "created_at_min", "created_at_max", "query"]:
                    if v := arguments.get(key):
                        params[key] = v
                r = await c.get(f"{base}/customers.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "shopify_create_customer":
                customer: dict[str, Any] = {"email": arguments["email"]}
                for key in ["first_name", "last_name", "phone", "tags", "note", "accepts_marketing", "addresses"]:
                    if v := arguments.get(key) is not None and arguments.get(key):
                        customer[key] = arguments[key]
                r = await c.post(f"{base}/customers.json", json={"customer": customer})
                r.raise_for_status()
                return r.json()

            elif tool_name == "shopify_list_collections":
                params = {"limit": arguments.get("limit", 50)}
                if pid := arguments.get("product_id"):
                    params["product_id"] = pid
                r = await c.get(f"{base}/custom_collections.json", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "shopify_update_inventory":
                payload = {
                    "location_id": arguments["location_id"],
                    "inventory_item_id": arguments["inventory_item_id"],
                    "available": arguments["available"],
                }
                r = await c.post(f"{base}/inventory_levels/set.json", json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("shopify_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
