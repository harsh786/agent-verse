"""Magento MCP server — Magento e-commerce products, orders, customers, and categories.

Environment:
  MAGENTO_ACCESS_TOKEN: Magento integration access token
  MAGENTO_BASE_URL: Magento store base URL, e.g. https://mystore.example.com
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "magento_list_products",
        "description": "List products in the Magento catalog with optional search criteria",
        "parameters": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string", "description": "Search term for product name or SKU"},
                "page_size": {"type": "integer", "description": "Number of products per page (max 300)", "default": 20},
                "current_page": {"type": "integer", "description": "Current page number", "default": 1},
                "status": {"type": "integer", "description": "Product status: 1=enabled, 2=disabled"},
            },
        },
    },
    {
        "name": "magento_create_product",
        "description": "Create a new simple product in the Magento catalog",
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "Unique product SKU"},
                "name": {"type": "string", "description": "Product name"},
                "price": {"type": "number", "description": "Product price"},
                "status": {"type": "integer", "description": "Product status: 1=enabled, 2=disabled", "default": 1},
                "type_id": {"type": "string", "description": "Product type: simple, configurable, virtual, bundle", "default": "simple"},
                "attribute_set_id": {"type": "integer", "description": "Attribute set ID (default 4 for Default)", "default": 4},
                "weight": {"type": "number", "description": "Product weight"},
            },
            "required": ["sku", "name", "price"],
        },
    },
    {
        "name": "magento_list_orders",
        "description": "List sales orders in Magento with optional status filter",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Order status filter: pending, processing, complete, canceled, holded, closed"},
                "page_size": {"type": "integer", "description": "Number of orders per page", "default": 20},
                "current_page": {"type": "integer", "description": "Current page number", "default": 1},
            },
        },
    },
    {
        "name": "magento_get_order",
        "description": "Get full details of a Magento order by order ID",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Magento order ID (entity_id)"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "magento_list_customers",
        "description": "List customers in Magento with optional search",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Filter customers by email"},
                "page_size": {"type": "integer", "description": "Number of customers per page", "default": 20},
                "current_page": {"type": "integer", "description": "Current page number", "default": 1},
            },
        },
    },
    {
        "name": "magento_get_category_tree",
        "description": "Get the full Magento product category tree",
        "parameters": {
            "type": "object",
            "properties": {
                "root_category_id": {"type": "integer", "description": "Root category ID to start from (default 1)", "default": 1},
                "depth": {"type": "integer", "description": "Depth of the category tree to retrieve", "default": 3},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("MAGENTO_ACCESS_TOKEN", "")
    base_url = os.getenv("MAGENTO_BASE_URL", "")
    if not access_token:
        return {"error": "MAGENTO_ACCESS_TOKEN not configured"}
    if not base_url:
        return {"error": "MAGENTO_BASE_URL not configured"}

    base = base_url.rstrip("/") + "/rest/V1"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "magento_list_products":
                params: dict[str, Any] = {
                    "searchCriteria[pageSize]": arguments.get("page_size", 20),
                    "searchCriteria[currentPage]": arguments.get("current_page", 1),
                }
                idx = 0
                if "search_query" in arguments:
                    params[f"searchCriteria[filterGroups][{idx}][filters][0][field]"] = "name"
                    params[f"searchCriteria[filterGroups][{idx}][filters][0][value]"] = f"%{arguments['search_query']}%"
                    params[f"searchCriteria[filterGroups][{idx}][filters][0][conditionType]"] = "like"
                    idx += 1
                if "status" in arguments:
                    params[f"searchCriteria[filterGroups][{idx}][filters][0][field]"] = "status"
                    params[f"searchCriteria[filterGroups][{idx}][filters][0][value]"] = arguments["status"]
                    params[f"searchCriteria[filterGroups][{idx}][filters][0][conditionType]"] = "eq"
                r = await client.get(f"{base}/products", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "products": [
                        {
                            "id": p.get("id"),
                            "sku": p.get("sku"),
                            "name": p.get("name"),
                            "price": p.get("price"),
                            "status": p.get("status"),
                            "type_id": p.get("type_id"),
                        }
                        for p in data.get("items", [])
                    ],
                    "total_count": data.get("total_count", 0),
                }

            elif tool_name == "magento_create_product":
                product: dict[str, Any] = {
                    "sku": arguments["sku"],
                    "name": arguments["name"],
                    "price": arguments["price"],
                    "status": arguments.get("status", 1),
                    "type_id": arguments.get("type_id", "simple"),
                    "attribute_set_id": arguments.get("attribute_set_id", 4),
                }
                if "weight" in arguments:
                    product["weight"] = arguments["weight"]
                r = await client.post(f"{base}/products", headers=headers, json={"product": product})
                r.raise_for_status()
                data = r.json()
                return {
                    "id": data.get("id"),
                    "sku": data.get("sku"),
                    "name": data.get("name"),
                }

            elif tool_name == "magento_list_orders":
                params = {
                    "searchCriteria[pageSize]": arguments.get("page_size", 20),
                    "searchCriteria[currentPage]": arguments.get("current_page", 1),
                }
                if "status" in arguments:
                    params["searchCriteria[filterGroups][0][filters][0][field]"] = "status"
                    params["searchCriteria[filterGroups][0][filters][0][value]"] = arguments["status"]
                    params["searchCriteria[filterGroups][0][filters][0][conditionType]"] = "eq"
                r = await client.get(f"{base}/orders", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "orders": [
                        {
                            "entity_id": o.get("entity_id"),
                            "increment_id": o.get("increment_id"),
                            "status": o.get("status"),
                            "grand_total": o.get("grand_total"),
                            "customer_email": o.get("customer_email"),
                            "created_at": o.get("created_at"),
                        }
                        for o in data.get("items", [])
                    ],
                    "total_count": data.get("total_count", 0),
                }

            elif tool_name == "magento_get_order":
                r = await client.get(f"{base}/orders/{arguments['order_id']}", headers=headers)
                r.raise_for_status()
                return r.json()

            elif tool_name == "magento_list_customers":
                params = {
                    "searchCriteria[pageSize]": arguments.get("page_size", 20),
                    "searchCriteria[currentPage]": arguments.get("current_page", 1),
                }
                if "email" in arguments:
                    params["searchCriteria[filterGroups][0][filters][0][field]"] = "email"
                    params["searchCriteria[filterGroups][0][filters][0][value]"] = arguments["email"]
                    params["searchCriteria[filterGroups][0][filters][0][conditionType]"] = "eq"
                r = await client.get(f"{base}/customers/search", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "customers": [
                        {
                            "id": c.get("id"),
                            "email": c.get("email"),
                            "firstname": c.get("firstname"),
                            "lastname": c.get("lastname"),
                            "created_at": c.get("created_at"),
                        }
                        for c in data.get("items", [])
                    ],
                    "total_count": data.get("total_count", 0),
                }

            elif tool_name == "magento_get_category_tree":
                params = {
                    "rootCategoryId": arguments.get("root_category_id", 1),
                    "depth": arguments.get("depth", 3),
                }
                r = await client.get(f"{base}/categories", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("magento_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
