"""Konnektive CRM MCP server — orders, customers, and subscriptions management.

Environment variables:
  KONNEKTIVE_LOGIN_ID: Konnektive API login ID
  KONNEKTIVE_PASSWORD: Konnektive API password
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

KONNEKTIVE_BASE = "https://api.konnektive.com"

TOOL_DEFINITIONS = [
    {
        "name": "konnektive_list_orders",
        "description": "List orders in Konnektive CRM with optional date range and status filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "startDate": {"type": "string", "description": "Start date (MM/DD/YYYY)"},
                "endDate": {"type": "string", "description": "End date (MM/DD/YYYY)"},
                "orderStatus": {
                    "type": "string",
                    "enum": ["COMPLETE", "CANCELLED", "REFUNDED", "PARTIAL"],
                    "description": "Filter by order status",
                },
                "resultsPerPage": {"type": "integer", "default": 25},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "konnektive_get_order",
        "description": "Get detailed information about a specific order in Konnektive",
        "parameters": {
            "type": "object",
            "properties": {
                "orderId": {"type": "string", "description": "Konnektive order ID"},
            },
            "required": ["orderId"],
        },
    },
    {
        "name": "konnektive_update_order_status",
        "description": "Update the status of an order in Konnektive CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "orderId": {"type": "string", "description": "Order ID to update"},
                "orderStatus": {
                    "type": "string",
                    "enum": ["COMPLETE", "CANCELLED", "REFUNDED"],
                    "description": "New order status",
                },
            },
            "required": ["orderId", "orderStatus"],
        },
    },
    {
        "name": "konnektive_list_customers",
        "description": "List customers in Konnektive CRM with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "startDate": {"type": "string", "description": "Registration start date (MM/DD/YYYY)"},
                "endDate": {"type": "string", "description": "Registration end date (MM/DD/YYYY)"},
                "emailAddress": {"type": "string", "description": "Filter by email"},
                "resultsPerPage": {"type": "integer", "default": 25},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "konnektive_get_customer",
        "description": "Get detailed information about a specific customer in Konnektive",
        "parameters": {
            "type": "object",
            "properties": {
                "customerId": {"type": "string", "description": "Konnektive customer ID"},
            },
            "required": ["customerId"],
        },
    },
    {
        "name": "konnektive_create_subscription",
        "description": "Create a new subscription for a customer in Konnektive CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "customerId": {"type": "string", "description": "Customer ID"},
                "campaignId": {"type": "string", "description": "Konnektive campaign ID"},
                "productId": {"type": "string", "description": "Product ID"},
                "productQty": {"type": "integer", "default": 1},
                "shipProfile": {"type": "string", "description": "Shipping profile code"},
                "nextBillDate": {"type": "string", "description": "Next billing date (MM/DD/YYYY)"},
            },
            "required": ["customerId", "campaignId", "productId"],
        },
    },
]


def _auth_params(login_id: str, password: str) -> dict[str, str]:
    return {"loginId": login_id, "password": password}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    login_id = os.getenv("KONNEKTIVE_LOGIN_ID", "")
    password = os.getenv("KONNEKTIVE_PASSWORD", "")
    if not login_id:
        return {"error": "KONNEKTIVE_LOGIN_ID not configured"}
    if not password:
        return {"error": "KONNEKTIVE_PASSWORD not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=KONNEKTIVE_BASE,
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        ) as c:
            auth = _auth_params(login_id, password)

            if tool_name == "konnektive_list_orders":
                params: dict[str, Any] = {
                    **auth,
                    "resultsPerPage": arguments.get("resultsPerPage", 25),
                    "page": arguments.get("page", 1),
                }
                for k in ("startDate", "endDate", "orderStatus"):
                    if k in arguments:
                        params[k] = arguments[k]
                r = await c.get("/order/query/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "konnektive_get_order":
                params = {**auth, "orderId": arguments["orderId"]}
                r = await c.get("/order/query/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "konnektive_update_order_status":
                body: dict[str, Any] = {
                    **auth,
                    "orderId": arguments["orderId"],
                    "orderStatus": arguments["orderStatus"],
                }
                r = await c.post("/order/update/", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "konnektive_list_customers":
                params = {
                    **auth,
                    "resultsPerPage": arguments.get("resultsPerPage", 25),
                    "page": arguments.get("page", 1),
                }
                for k in ("startDate", "endDate", "emailAddress"):
                    if k in arguments:
                        params[k] = arguments[k]
                r = await c.get("/customer/query/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "konnektive_get_customer":
                params = {**auth, "customerId": arguments["customerId"]}
                r = await c.get("/customer/query/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "konnektive_create_subscription":
                body = {
                    **auth,
                    "customerId": arguments["customerId"],
                    "campaignId": arguments["campaignId"],
                    "productId": arguments["productId"],
                    "productQty": arguments.get("productQty", 1),
                }
                for k in ("shipProfile", "nextBillDate"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/subscription/create/", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("konnektive_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
