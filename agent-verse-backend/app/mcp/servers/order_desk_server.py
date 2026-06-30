"""Order Desk MCP server — Order Desk order management, inventory, and shipments.

Environment:
  ORDER_DESK_STORE_ID: Order Desk store ID
  ORDER_DESK_API_KEY: Order Desk API key from Store Settings > Integration
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://app.orderdesk.me/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "order_desk_list_orders",
        "description": "List orders in Order Desk with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "fulfillment_status": {"type": "string", "description": "Filter by fulfillment status"},
                "payment_status": {"type": "string", "description": "Filter by payment status"},
                "source_name": {"type": "string", "description": "Filter by order source name"},
                "limit": {"type": "integer", "description": "Number of orders to return (max 200)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "order_desk_update_order",
        "description": "Update an existing order in Order Desk",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order Desk order ID"},
                "fulfillment_status": {"type": "string", "description": "New fulfillment status"},
                "payment_status": {"type": "string", "description": "New payment status"},
                "folder_name": {"type": "string", "description": "Move order to this folder"},
                "custom_data": {"type": "object", "description": "Custom key-value data for the order"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "order_desk_list_inventory",
        "description": "List inventory items in Order Desk",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Filter by inventory item name"},
                "code": {"type": "string", "description": "Filter by item code/SKU"},
                "limit": {"type": "integer", "description": "Number of items to return (max 200)", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "order_desk_update_inventory",
        "description": "Update inventory item quantity or details in Order Desk",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "Order Desk inventory item ID"},
                "stock": {"type": "integer", "description": "Updated stock quantity"},
                "name": {"type": "string", "description": "Updated item name"},
                "price": {"type": "number", "description": "Updated item price"},
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "order_desk_list_shipments",
        "description": "List shipments associated with an order in Order Desk",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Filter shipments by order ID"},
                "carrier": {"type": "string", "description": "Filter by carrier name"},
                "limit": {"type": "integer", "description": "Number of shipments to return", "default": 20},
                "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
            },
        },
    },
    {
        "name": "order_desk_create_shipment",
        "description": "Create a shipment record for an Order Desk order",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order Desk order ID"},
                "carrier": {"type": "string", "description": "Shipping carrier name"},
                "tracking_number": {"type": "string", "description": "Shipment tracking number"},
                "tracking_url": {"type": "string", "description": "Full tracking URL"},
                "send_notification": {"type": "boolean", "description": "Send shipping notification to customer", "default": True},
            },
            "required": ["order_id", "carrier", "tracking_number"],
        },
    },
]


def _headers() -> dict[str, str]:
    store_id = os.getenv("ORDER_DESK_STORE_ID", "")
    api_key = os.getenv("ORDER_DESK_API_KEY", "")
    return {
        "ORDERDESK-STORE-ID": store_id,
        "ORDERDESK-API-KEY": api_key,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    store_id = os.getenv("ORDER_DESK_STORE_ID", "")
    api_key = os.getenv("ORDER_DESK_API_KEY", "")
    if not store_id:
        return {"error": "ORDER_DESK_STORE_ID not configured"}
    if not api_key:
        return {"error": "ORDER_DESK_API_KEY not configured"}

    headers = {
        "ORDERDESK-STORE-ID": store_id,
        "ORDERDESK-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "order_desk_list_orders":
                params: dict[str, Any] = {
                    "count": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "fulfillment_status" in arguments:
                    params["fulfillment_status"] = arguments["fulfillment_status"]
                if "payment_status" in arguments:
                    params["payment_status"] = arguments["payment_status"]
                if "source_name" in arguments:
                    params["source_name"] = arguments["source_name"]
                r = await client.get(f"{BASE}/orders", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "orders": [
                        {
                            "id": o.get("id"),
                            "order_id": o.get("order_id"),
                            "fulfillment_status": o.get("fulfillment_status"),
                            "payment_status": o.get("payment_status"),
                            "email": o.get("email"),
                            "total": o.get("order_total"),
                        }
                        for o in data.get("orders", [])
                    ],
                    "count": data.get("count", 0),
                }

            elif tool_name == "order_desk_update_order":
                payload: dict[str, Any] = {}
                for field in ("fulfillment_status", "payment_status", "folder_name"):
                    if field in arguments:
                        payload[field] = arguments[field]
                if "custom_data" in arguments:
                    payload.update(arguments["custom_data"])
                r = await client.put(
                    f"{BASE}/orders/{arguments['order_id']}",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                return {"updated": True, "order_id": arguments["order_id"], "response": data}

            elif tool_name == "order_desk_list_inventory":
                params = {
                    "count": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "name" in arguments:
                    params["name"] = arguments["name"]
                if "code" in arguments:
                    params["code"] = arguments["code"]
                r = await client.get(f"{BASE}/inventory-items", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "items": data.get("inventory_items", []),
                    "count": data.get("count", 0),
                }

            elif tool_name == "order_desk_update_inventory":
                payload = {}
                for field in ("stock", "name", "price"):
                    if field in arguments:
                        payload[field] = arguments[field]
                r = await client.put(
                    f"{BASE}/inventory-items/{arguments['item_id']}",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return {"updated": True, "item_id": arguments["item_id"]}

            elif tool_name == "order_desk_list_shipments":
                params = {
                    "count": arguments.get("limit", 20),
                    "offset": arguments.get("offset", 0),
                }
                if "order_id" in arguments:
                    params["order_id"] = arguments["order_id"]
                if "carrier" in arguments:
                    params["carrier"] = arguments["carrier"]
                r = await client.get(f"{BASE}/shipments", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "shipments": data.get("shipments", []),
                    "count": data.get("count", 0),
                }

            elif tool_name == "order_desk_create_shipment":
                payload = {
                    "order_id": arguments["order_id"],
                    "carrier": arguments["carrier"],
                    "tracking_number": arguments["tracking_number"],
                    "send_notification": arguments.get("send_notification", True),
                }
                if "tracking_url" in arguments:
                    payload["tracking_url"] = arguments["tracking_url"]
                r = await client.post(f"{BASE}/shipments", headers=headers, json=payload)
                r.raise_for_status()
                data = r.json()
                return {
                    "created": True,
                    "shipment_id": data.get("shipment", {}).get("id"),
                    "order_id": arguments["order_id"],
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("order_desk_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
