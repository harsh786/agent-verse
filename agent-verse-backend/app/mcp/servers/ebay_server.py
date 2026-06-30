"""eBay MCP server — eBay marketplace item search, orders, and selling statistics.

Environment:
  EBAY_APP_ID: eBay application client ID (App ID)
  EBAY_OAUTH_TOKEN: OAuth2 user/app token for authenticated API calls
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.ebay.com/buy/browse/v1"

TOOL_DEFINITIONS = [
    {
        "name": "ebay_search_items",
        "description": "Search for eBay items using a keyword query",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search keyword(s)"},
                "category_ids": {"type": "string", "description": "Comma-separated category IDs to filter results"},
                "price_min": {"type": "number", "description": "Minimum price filter"},
                "price_max": {"type": "number", "description": "Maximum price filter"},
                "limit": {"type": "integer", "description": "Number of items to return (max 200)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
            "required": ["q"],
        },
    },
    {
        "name": "ebay_get_item",
        "description": "Get full details for a specific eBay item by item ID",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "eBay item ID (e.g. v1|12345|0)"},
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "ebay_list_user_orders",
        "description": "List orders for the authenticated eBay seller account",
        "parameters": {
            "type": "object",
            "properties": {
                "order_ids": {"type": "string", "description": "Comma-separated order IDs to filter"},
                "filter": {"type": "string", "description": "Filter string e.g. orderfulfillmentstatus:{NOT_STARTED}"},
                "limit": {"type": "integer", "description": "Number of orders to return (max 200)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "ebay_get_selling_stats",
        "description": "Get selling account summary statistics via the Sell Analytics API",
        "parameters": {
            "type": "object",
            "properties": {
                "metric_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Metric keys e.g. SALES_CONVERSION_RATE, TRANSACTION, LISTING_IMPRESSION_TOTAL",
                },
            },
        },
    },
    {
        "name": "ebay_find_items_by_keywords",
        "description": "Find eBay items by keywords using the Finding API",
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {"type": "string", "description": "Keyword string to search for"},
                "sort_order": {"type": "string", "description": "Sort order: BestMatch, CurrentPriceHighest, PricePlusShippingLowest", "default": "BestMatch"},
                "page_number": {"type": "integer", "description": "Page number for pagination", "default": 1},
                "entries_per_page": {"type": "integer", "description": "Results per page (max 100)", "default": 20},
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "ebay_get_categories",
        "description": "Get the eBay category tree or subtree for a specific category",
        "parameters": {
            "type": "object",
            "properties": {
                "category_tree_id": {"type": "string", "description": "Category tree ID (e.g. 0 for US)", "default": "0"},
                "category_id": {"type": "string", "description": "Parent category ID to get subtree (optional)"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    app_id = os.getenv("EBAY_APP_ID", "")
    oauth_token = os.getenv("EBAY_OAUTH_TOKEN", "")
    if not app_id:
        return {"error": "EBAY_APP_ID not configured"}
    if not oauth_token:
        return {"error": "EBAY_OAUTH_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {oauth_token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "ebay_search_items":
                params: dict[str, Any] = {
                    "q": arguments["q"],
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "category_ids" in arguments:
                    params["category_ids"] = arguments["category_ids"]
                if "price_min" in arguments or "price_max" in arguments:
                    price_parts = []
                    if "price_min" in arguments:
                        price_parts.append(f"price:[{arguments['price_min']} TO *]")
                    if "price_max" in arguments:
                        price_parts.append(f"price:[* TO {arguments['price_max']}]")
                    params["filter"] = ",".join(price_parts)
                r = await client.get(f"{BASE}/item_summary/search", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "items": [
                        {
                            "item_id": i.get("itemId"),
                            "title": i.get("title"),
                            "price": i.get("price"),
                            "condition": i.get("condition"),
                            "item_location": i.get("itemLocation"),
                        }
                        for i in data.get("itemSummaries", [])
                    ],
                    "total": data.get("total", 0),
                }

            elif tool_name == "ebay_get_item":
                r = await client.get(
                    f"{BASE}/item/{arguments['item_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "item_id": data.get("itemId"),
                    "title": data.get("title"),
                    "price": data.get("price"),
                    "condition": data.get("condition"),
                    "description": data.get("shortDescription"),
                    "seller": data.get("seller"),
                    "shipping_options": data.get("shippingOptions", []),
                    "return_terms": data.get("returnTerms"),
                }

            elif tool_name == "ebay_list_user_orders":
                params = {
                    "limit": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "order_ids" in arguments:
                    params["orderIds"] = arguments["order_ids"]
                if "filter" in arguments:
                    params["filter"] = arguments["filter"]
                r = await client.get(
                    "https://api.ebay.com/sell/fulfillment/v1/order",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "orders": data.get("orders", []),
                    "total": data.get("total", 0),
                }

            elif tool_name == "ebay_get_selling_stats":
                metric_keys = arguments.get("metric_keys", ["TRANSACTION", "LISTING_IMPRESSION_TOTAL"])
                r = await client.get(
                    "https://api.ebay.com/sell/analytics/v1/seller_standards_profile",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "ebay_find_items_by_keywords":
                params = {
                    "keywords": arguments["keywords"],
                    "sortOrder": arguments.get("sort_order", "BestMatch"),
                    "paginationInput.pageNumber": str(arguments.get("page_number", 1)),
                    "paginationInput.entriesPerPage": str(arguments.get("entries_per_page", 20)),
                    "OPERATION-NAME": "findItemsByKeywords",
                    "SERVICE-VERSION": "1.0.0",
                    "SECURITY-APPNAME": app_id,
                    "RESPONSE-DATA-FORMAT": "JSON",
                }
                r = await client.get(
                    "https://svcs.ebay.com/services/search/FindingService/v1",
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "ebay_get_categories":
                tree_id = arguments.get("category_tree_id", "0")
                if "category_id" in arguments:
                    r = await client.get(
                        f"https://api.ebay.com/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_subtree",
                        headers=headers,
                        params={"category_id": arguments["category_id"]},
                    )
                else:
                    r = await client.get(
                        f"https://api.ebay.com/commerce/taxonomy/v1/category_tree/{tree_id}",
                        headers=headers,
                    )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("ebay_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
