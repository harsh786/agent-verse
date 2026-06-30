"""Gumroad MCP server — Gumroad digital product sales, subscriptions, and license management.

Environment:
  GUMROAD_ACCESS_TOKEN: Gumroad OAuth2 access token from API settings
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.gumroad.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "gumroad_list_products",
        "description": "List all products in the Gumroad account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "gumroad_create_product",
        "description": "Create a new digital product on Gumroad",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Product name"},
                "price": {"type": "integer", "description": "Price in cents (0 for pay-what-you-want)"},
                "url": {"type": "string", "description": "Product URL (custom_permalink)"},
                "description": {"type": "string", "description": "Product description"},
                "published": {"type": "boolean", "description": "Whether to publish immediately", "default": False},
                "require_shipping": {"type": "boolean", "description": "Whether shipping address is required", "default": False},
            },
            "required": ["name", "price"],
        },
    },
    {
        "name": "gumroad_list_sales",
        "description": "List sales for the Gumroad account",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Filter by product ID"},
                "after": {"type": "string", "description": "Only return sales after this ISO 8601 datetime"},
                "before": {"type": "string", "description": "Only return sales before this ISO 8601 datetime"},
                "page": {"type": "integer", "description": "Page number for pagination", "default": 1},
            },
        },
    },
    {
        "name": "gumroad_get_sale",
        "description": "Get details of a specific Gumroad sale by sale ID",
        "parameters": {
            "type": "object",
            "properties": {
                "sale_id": {"type": "string", "description": "Gumroad sale ID"},
            },
            "required": ["sale_id"],
        },
    },
    {
        "name": "gumroad_list_subscribers",
        "description": "List subscribers for a Gumroad membership/subscription product",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product ID to list subscribers for"},
                "page": {"type": "integer", "description": "Page number", "default": 1},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "gumroad_enable_license",
        "description": "Enable or verify a Gumroad license key for a product",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Gumroad product ID or permalink"},
                "license_key": {"type": "string", "description": "License key to enable/verify"},
                "increment_uses_count": {"type": "boolean", "description": "Increment the uses count", "default": True},
            },
            "required": ["product_id", "license_key"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("GUMROAD_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "GUMROAD_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "gumroad_list_products":
                r = await client.get(
                    f"{BASE}/products",
                    headers={**headers, "Content-Type": "application/json"},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "success": data.get("success"),
                    "products": [
                        {
                            "id": p.get("id"),
                            "name": p.get("name"),
                            "price": p.get("price"),
                            "sales_count": p.get("sales_count"),
                            "published": p.get("published"),
                        }
                        for p in data.get("products", [])
                    ],
                }

            elif tool_name == "gumroad_create_product":
                data_payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "price": arguments["price"],
                }
                if "url" in arguments:
                    data_payload["url"] = arguments["url"]
                if "description" in arguments:
                    data_payload["description"] = arguments["description"]
                if "published" in arguments:
                    data_payload["published"] = "true" if arguments["published"] else "false"
                if "require_shipping" in arguments:
                    data_payload["require_shipping"] = "true" if arguments["require_shipping"] else "false"
                r = await client.post(f"{BASE}/products", headers=headers, data=data_payload)
                r.raise_for_status()
                result = r.json()
                product = result.get("product", {})
                return {
                    "success": result.get("success"),
                    "id": product.get("id"),
                    "name": product.get("name"),
                }

            elif tool_name == "gumroad_list_sales":
                params: dict[str, Any] = {"page": arguments.get("page", 1)}
                if "product_id" in arguments:
                    params["product_id"] = arguments["product_id"]
                if "after" in arguments:
                    params["after"] = arguments["after"]
                if "before" in arguments:
                    params["before"] = arguments["before"]
                r = await client.get(
                    f"{BASE}/sales",
                    headers={**headers, "Content-Type": "application/json"},
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "success": data.get("success"),
                    "sales": [
                        {
                            "id": s.get("id"),
                            "product_name": s.get("product_name"),
                            "price": s.get("price"),
                            "gumroad_fee": s.get("gumroad_fee"),
                            "created_at": s.get("created_at"),
                            "purchaser_id": s.get("purchaser_id"),
                        }
                        for s in data.get("sales", [])
                    ],
                    "next_page_url": data.get("next_page_url"),
                }

            elif tool_name == "gumroad_get_sale":
                r = await client.get(
                    f"{BASE}/sales/{arguments['sale_id']}",
                    headers={**headers, "Content-Type": "application/json"},
                )
                r.raise_for_status()
                data = r.json()
                return {"success": data.get("success"), "sale": data.get("sale", {})}

            elif tool_name == "gumroad_list_subscribers":
                r = await client.get(
                    f"{BASE}/products/{arguments['product_id']}/subscribers",
                    headers={**headers, "Content-Type": "application/json"},
                    params={"page": arguments.get("page", 1)},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "success": data.get("success"),
                    "subscribers": data.get("subscribers", []),
                }

            elif tool_name == "gumroad_enable_license":
                r = await client.post(
                    f"{BASE}/licenses/enable",
                    headers=headers,
                    data={
                        "product_id": arguments["product_id"],
                        "license_key": arguments["license_key"],
                        "increment_uses_count": "true" if arguments.get("increment_uses_count", True) else "false",
                    },
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("gumroad_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
