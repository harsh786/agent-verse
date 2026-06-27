"""WooCommerce MCP server — WordPress e-commerce store management.

Environment:
  WOOCOMMERCE_URL:              Store base URL (e.g. 'https://mystore.com')
  WOOCOMMERCE_CONSUMER_KEY:     REST API consumer key (ck_...)
  WOOCOMMERCE_CONSUMER_SECRET:  REST API consumer secret (cs_...)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

WC_API_VERSION = "wc/v3"

TOOL_DEFINITIONS = [
    {
        "name": "woo_list_products",
        "description": "List WooCommerce products",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 20},
                "page": {"type": "integer", "default": 1},
                "status": {"type": "string", "enum": ["any", "draft", "pending", "private", "publish"]},
                "category": {"type": "string", "description": "Category ID filter"},
                "search": {"type": "string"},
            },
        },
    },
    {
        "name": "woo_get_product",
        "description": "Get a WooCommerce product by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "woo_create_product",
        "description": "Create a new WooCommerce product",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["simple", "grouped", "external", "variable"],
                    "default": "simple",
                },
                "regular_price": {"type": "string", "description": "Price as string, e.g. '19.99'"},
                "description": {"type": "string"},
                "short_description": {"type": "string"},
                "sku": {"type": "string"},
                "status": {"type": "string", "default": "draft"},
                "categories": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["name"],
        },
    },
    {
        "name": "woo_list_orders",
        "description": "List WooCommerce orders",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 20},
                "page": {"type": "integer", "default": 1},
                "status": {
                    "type": "string",
                    "enum": ["any", "pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"],
                },
                "customer": {"type": "integer", "description": "Customer user ID"},
                "after": {"type": "string", "description": "ISO 8601 date — orders created after"},
                "before": {"type": "string", "description": "ISO 8601 date — orders created before"},
            },
        },
    },
    {
        "name": "woo_get_order",
        "description": "Get a WooCommerce order by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "woo_update_order",
        "description": "Update a WooCommerce order status or metadata",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
                "status": {"type": "string"},
                "customer_note": {"type": "string"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "woo_list_customers",
        "description": "List WooCommerce customers",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 20},
                "page": {"type": "integer", "default": 1},
                "search": {"type": "string"},
                "email": {"type": "string"},
            },
        },
    },
]


def _auth() -> tuple[str, str]:
    return (
        os.getenv("WOOCOMMERCE_CONSUMER_KEY", ""),
        os.getenv("WOOCOMMERCE_CONSUMER_SECRET", ""),
    )


def _base() -> str:
    url = os.getenv("WOOCOMMERCE_URL", "").rstrip("/")
    return f"{url}/{WC_API_VERSION}"


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    wc_url = os.getenv("WOOCOMMERCE_URL", "")
    ck = os.getenv("WOOCOMMERCE_CONSUMER_KEY", "")
    cs = os.getenv("WOOCOMMERCE_CONSUMER_SECRET", "")
    if not all([wc_url, ck, cs]):
        return {
            "error": "WOOCOMMERCE_URL, WOOCOMMERCE_CONSUMER_KEY, "
            "and WOOCOMMERCE_CONSUMER_SECRET must be configured"
        }

    base = _base()

    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=30.0) as c:
            if tool_name == "woo_list_products":
                params: dict[str, Any] = {
                    "per_page": arguments.get("per_page", 20),
                    "page": arguments.get("page", 1),
                }
                for key in ["status", "category", "search"]:
                    if v := arguments.get(key):
                        params[key] = v
                r = await c.get(f"{base}/products", params=params)
                r.raise_for_status()
                return {"products": r.json()}

            elif tool_name == "woo_get_product":
                r = await c.get(f"{base}/products/{arguments['product_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "woo_create_product":
                payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "type": arguments.get("type", "simple"),
                    "status": arguments.get("status", "draft"),
                }
                for key in ["regular_price", "description", "short_description", "sku", "categories"]:
                    if v := arguments.get(key):
                        payload[key] = v
                r = await c.post(f"{base}/products", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "woo_list_orders":
                params = {
                    "per_page": arguments.get("per_page", 20),
                    "page": arguments.get("page", 1),
                }
                for key in ["status", "customer", "after", "before"]:
                    if v := arguments.get(key):
                        params[key] = v
                r = await c.get(f"{base}/orders", params=params)
                r.raise_for_status()
                return {"orders": r.json()}

            elif tool_name == "woo_get_order":
                r = await c.get(f"{base}/orders/{arguments['order_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "woo_update_order":
                oid = arguments["order_id"]
                payload = {}
                if status := arguments.get("status"):
                    payload["status"] = status
                if note := arguments.get("customer_note"):
                    payload["customer_note"] = note
                r = await c.put(f"{base}/orders/{oid}", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "woo_list_customers":
                params = {
                    "per_page": arguments.get("per_page", 20),
                    "page": arguments.get("page", 1),
                }
                for key in ["search", "email"]:
                    if v := arguments.get(key):
                        params[key] = v
                r = await c.get(f"{base}/customers", params=params)
                r.raise_for_status()
                return {"customers": r.json()}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("woocommerce_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
