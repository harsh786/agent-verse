"""ShipStation MCP server — ShipStation shipping, orders, shipments, and labels.

Environment:
  SHIPSTATION_API_KEY: ShipStation API key from Account Settings > API Settings
  SHIPSTATION_API_SECRET: ShipStation API secret
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://ssapi.shipstation.com"

TOOL_DEFINITIONS = [
    {
        "name": "shipstation_list_orders",
        "description": "List orders in ShipStation with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "order_status": {"type": "string", "description": "Filter by status: awaiting_payment, awaiting_shipment, shipped, on_hold, cancelled"},
                "store_id": {"type": "integer", "description": "Filter by ShipStation store ID"},
                "page": {"type": "integer", "description": "Page number for pagination", "default": 1},
                "page_size": {"type": "integer", "description": "Number of orders per page (max 500)", "default": 25},
            },
        },
    },
    {
        "name": "shipstation_create_shipment",
        "description": "Create a shipment for an order in ShipStation",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer", "description": "ShipStation order ID"},
                "carrier_code": {"type": "string", "description": "Carrier code e.g. stamps_com, fedex, ups"},
                "service_code": {"type": "string", "description": "Service code e.g. usps_first_class_mail"},
                "package_code": {"type": "string", "description": "Package code e.g. package, large_envelope_or_flat"},
                "weight": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "number"},
                        "units": {"type": "string", "description": "ounces, pounds, grams"},
                    },
                    "description": "Shipment weight",
                },
                "tracking_number": {"type": "string", "description": "Pre-existing tracking number"},
            },
            "required": ["order_id", "carrier_code", "service_code"],
        },
    },
    {
        "name": "shipstation_get_shipment",
        "description": "Get details of a specific ShipStation shipment",
        "parameters": {
            "type": "object",
            "properties": {
                "shipment_id": {"type": "integer", "description": "ShipStation shipment ID"},
            },
            "required": ["shipment_id"],
        },
    },
    {
        "name": "shipstation_list_stores",
        "description": "List all stores connected to the ShipStation account",
        "parameters": {
            "type": "object",
            "properties": {
                "show_inactive": {"type": "boolean", "description": "Include inactive stores", "default": False},
            },
        },
    },
    {
        "name": "shipstation_list_carriers",
        "description": "List all shipping carriers available in the ShipStation account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "shipstation_create_label",
        "description": "Create a shipping label for a ShipStation order",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer", "description": "ShipStation order ID"},
                "carrier_code": {"type": "string", "description": "Carrier code e.g. stamps_com, fedex, ups"},
                "service_code": {"type": "string", "description": "Service code for the carrier"},
                "package_code": {"type": "string", "description": "Package type code"},
                "confirmation": {"type": "string", "description": "Confirmation type: none, delivery, signature, adult_signature"},
                "ship_date": {"type": "string", "description": "Ship date in YYYY-MM-DD format"},
                "weight": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "number"},
                        "units": {"type": "string"},
                    },
                },
                "test_label": {"type": "boolean", "description": "Create a test label (no charge)", "default": False},
            },
            "required": ["order_id", "carrier_code", "service_code"],
        },
    },
]


def _auth_header() -> str:
    api_key = os.getenv("SHIPSTATION_API_KEY", "")
    api_secret = os.getenv("SHIPSTATION_API_SECRET", "")
    token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    return f"Basic {token}"


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("SHIPSTATION_API_KEY", "")
    api_secret = os.getenv("SHIPSTATION_API_SECRET", "")
    if not api_key:
        return {"error": "SHIPSTATION_API_KEY not configured"}
    if not api_secret:
        return {"error": "SHIPSTATION_API_SECRET not configured"}

    token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "shipstation_list_orders":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "pageSize": arguments.get("page_size", 25),
                }
                if "order_status" in arguments:
                    params["orderStatus"] = arguments["order_status"]
                if "store_id" in arguments:
                    params["storeId"] = arguments["store_id"]
                r = await client.get(f"{BASE}/orders", headers=headers, params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "orders": [
                        {
                            "order_id": o.get("orderId"),
                            "order_number": o.get("orderNumber"),
                            "order_status": o.get("orderStatus"),
                            "order_total": o.get("orderTotal"),
                            "customer_email": o.get("customerEmail"),
                            "order_date": o.get("orderDate"),
                        }
                        for o in data.get("orders", [])
                    ],
                    "total": data.get("total", 0),
                    "page": data.get("page", 1),
                    "pages": data.get("pages", 1),
                }

            elif tool_name == "shipstation_create_shipment":
                payload: dict[str, Any] = {
                    "orderId": arguments["order_id"],
                    "carrierCode": arguments["carrier_code"],
                    "serviceCode": arguments["service_code"],
                }
                if "package_code" in arguments:
                    payload["packageCode"] = arguments["package_code"]
                if "weight" in arguments:
                    payload["weight"] = arguments["weight"]
                if "tracking_number" in arguments:
                    payload["trackingNumber"] = arguments["tracking_number"]
                r = await client.post(f"{BASE}/shipments/createlabel", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "shipstation_get_shipment":
                r = await client.get(
                    f"{BASE}/shipments",
                    headers=headers,
                    params={"shipmentId": arguments["shipment_id"]},
                )
                r.raise_for_status()
                data = r.json()
                shipments = data.get("shipments", [])
                return shipments[0] if shipments else {"error": "Shipment not found"}

            elif tool_name == "shipstation_list_stores":
                params = {}
                if "show_inactive" in arguments:
                    params["showInactive"] = str(arguments["show_inactive"]).lower()
                r = await client.get(f"{BASE}/stores", headers=headers, params=params)
                r.raise_for_status()
                return {"stores": r.json()}

            elif tool_name == "shipstation_list_carriers":
                r = await client.get(f"{BASE}/carriers", headers=headers)
                r.raise_for_status()
                return {"carriers": r.json()}

            elif tool_name == "shipstation_create_label":
                payload = {
                    "orderId": arguments["order_id"],
                    "carrierCode": arguments["carrier_code"],
                    "serviceCode": arguments["service_code"],
                    "testLabel": arguments.get("test_label", False),
                }
                for field in ("package_code", "confirmation", "ship_date", "weight"):
                    if field in arguments:
                        camel = "packageCode" if field == "package_code" else (
                            "shipDate" if field == "ship_date" else field
                        )
                        payload[camel] = arguments[field]
                r = await client.post(f"{BASE}/orders/createlabelfororder", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("shipstation_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
