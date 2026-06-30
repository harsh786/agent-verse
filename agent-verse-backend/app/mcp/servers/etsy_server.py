"""Etsy MCP server — Etsy marketplace shops, listings, and orders management.

Environment:
  ETSY_API_KEY: Etsy API key (keystring) from the Etsy developer portal
  ETSY_ACCESS_TOKEN: OAuth2 access token for authenticated requests
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://openapi.etsy.com/v3"

TOOL_DEFINITIONS = [
    {
        "name": "etsy_list_shops",
        "description": "List Etsy shops for the authenticated user or search shops by name",
        "parameters": {
            "type": "object",
            "properties": {
                "shop_name": {"type": "string", "description": "Shop name to search for (optional)"},
                "limit": {"type": "integer", "description": "Number of results to return (max 100)", "default": 25},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "etsy_list_listings",
        "description": "List active listings for an Etsy shop",
        "parameters": {
            "type": "object",
            "properties": {
                "shop_id": {"type": "string", "description": "Etsy shop ID or shop name"},
                "state": {"type": "string", "description": "Listing state: active, inactive, sold_out, draft, expired", "default": "active"},
                "limit": {"type": "integer", "description": "Number of listings to return (max 100)", "default": 25},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
            "required": ["shop_id"],
        },
    },
    {
        "name": "etsy_create_listing",
        "description": "Create a new draft listing in an Etsy shop",
        "parameters": {
            "type": "object",
            "properties": {
                "shop_id": {"type": "string", "description": "Etsy shop ID"},
                "title": {"type": "string", "description": "Listing title (max 140 characters)"},
                "description": {"type": "string", "description": "Listing description"},
                "price": {"type": "number", "description": "Listing price in the shop currency"},
                "quantity": {"type": "integer", "description": "Available quantity", "default": 1},
                "taxonomy_id": {"type": "integer", "description": "Etsy taxonomy/category ID"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Up to 13 tags"},
            },
            "required": ["shop_id", "title", "description", "price", "quantity", "taxonomy_id"],
        },
    },
    {
        "name": "etsy_update_listing",
        "description": "Update an existing Etsy listing",
        "parameters": {
            "type": "object",
            "properties": {
                "shop_id": {"type": "string", "description": "Etsy shop ID"},
                "listing_id": {"type": "string", "description": "Etsy listing ID"},
                "title": {"type": "string", "description": "Updated listing title"},
                "description": {"type": "string", "description": "Updated listing description"},
                "price": {"type": "number", "description": "Updated price"},
                "quantity": {"type": "integer", "description": "Updated available quantity"},
                "state": {"type": "string", "description": "New state: active, inactive"},
            },
            "required": ["shop_id", "listing_id"],
        },
    },
    {
        "name": "etsy_list_orders",
        "description": "List orders (receipts) for an Etsy shop",
        "parameters": {
            "type": "object",
            "properties": {
                "shop_id": {"type": "string", "description": "Etsy shop ID"},
                "was_paid": {"type": "boolean", "description": "Filter by paid status"},
                "was_shipped": {"type": "boolean", "description": "Filter by shipped status"},
                "limit": {"type": "integer", "description": "Number of orders to return (max 100)", "default": 25},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
            "required": ["shop_id"],
        },
    },
    {
        "name": "etsy_get_shop_stats",
        "description": "Get statistics and metrics for an Etsy shop",
        "parameters": {
            "type": "object",
            "properties": {
                "shop_id": {"type": "string", "description": "Etsy shop ID"},
            },
            "required": ["shop_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("ETSY_API_KEY", "")
    access_token = os.getenv("ETSY_ACCESS_TOKEN", "")
    if not api_key:
        return {"error": "ETSY_API_KEY not configured"}
    if not access_token:
        return {"error": "ETSY_ACCESS_TOKEN not configured"}

    headers = {
        "x-api-key": api_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "etsy_list_shops":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                if "shop_name" in arguments:
                    params["shop_name"] = arguments["shop_name"]
                r = await client.get(f"{BASE}/application/shops", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "shops": [
                        {
                            "shop_id": s.get("shop_id"),
                            "shop_name": s.get("shop_name"),
                            "listing_active_count": s.get("listing_active_count"),
                            "currency_code": s.get("currency_code"),
                        }
                        for s in data.get("results", [])
                    ],
                    "count": data.get("count", 0),
                }

            elif tool_name == "etsy_list_listings":
                params = {
                    "state": arguments.get("state", "active"),
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                r = await client.get(
                    f"{BASE}/application/shops/{arguments['shop_id']}/listings",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "listings": [
                        {
                            "listing_id": l.get("listing_id"),
                            "title": l.get("title"),
                            "price": l.get("price"),
                            "quantity": l.get("quantity"),
                            "state": l.get("state"),
                        }
                        for l in data.get("results", [])
                    ],
                    "count": data.get("count", 0),
                }

            elif tool_name == "etsy_create_listing":
                payload: dict[str, Any] = {
                    "title": arguments["title"],
                    "description": arguments["description"],
                    "price": arguments["price"],
                    "quantity": arguments.get("quantity", 1),
                    "taxonomy_id": arguments["taxonomy_id"],
                    "who_made": "i_did",
                    "when_made": "made_to_order",
                    "is_supply": False,
                }
                if "tags" in arguments:
                    payload["tags"] = arguments["tags"][:13]
                r = await client.post(
                    f"{BASE}/application/shops/{arguments['shop_id']}/listings",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "listing_id": data.get("listing_id"),
                    "title": data.get("title"),
                    "state": data.get("state"),
                }

            elif tool_name == "etsy_update_listing":
                payload = {}
                for field in ("title", "description", "price", "quantity", "state"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.patch(
                    f"{BASE}/application/shops/{arguments['shop_id']}/listings/{arguments['listing_id']}",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "listing_id": data.get("listing_id"),
                    "title": data.get("title"),
                    "state": data.get("state"),
                }

            elif tool_name == "etsy_list_orders":
                params = {
                    "limit": arguments.get("limit", 25),
                    "offset": arguments.get("offset", 0),
                }
                if "was_paid" in arguments:
                    params["was_paid"] = arguments["was_paid"]
                if "was_shipped" in arguments:
                    params["was_shipped"] = arguments["was_shipped"]
                r = await client.get(
                    f"{BASE}/application/shops/{arguments['shop_id']}/receipts",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "orders": data.get("results", []),
                    "count": data.get("count", 0),
                }

            elif tool_name == "etsy_get_shop_stats":
                r = await client.get(
                    f"{BASE}/application/shops/{arguments['shop_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "shop_id": data.get("shop_id"),
                    "shop_name": data.get("shop_name"),
                    "listing_active_count": data.get("listing_active_count"),
                    "digital_listing_count": data.get("digital_listing_count"),
                    "num_favorers": data.get("num_favorers"),
                    "review_count": data.get("review_count"),
                    "review_average": data.get("review_average"),
                    "currency_code": data.get("currency_code"),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("etsy_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
