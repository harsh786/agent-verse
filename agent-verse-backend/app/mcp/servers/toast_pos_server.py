"""Toast POS MCP server — restaurant orders, menu management, and payments.

Environment:
  TOAST_CLIENT_ID: Toast API client ID
  TOAST_CLIENT_SECRET: Toast API client secret
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://ws-api.toasttab.com"
AUTH_URL = "https://ws-api.toasttab.com/authentication/v1/authentication/login"

TOOL_DEFINITIONS = [
    {
        "name": "toast_list_restaurants",
        "description": "List Toast restaurant locations accessible with the credentials",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "pageSize": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "toast_list_orders",
        "description": "List orders for a Toast restaurant location within a time range",
        "parameters": {
            "type": "object",
            "properties": {
                "restaurant_guid": {"type": "string", "description": "Toast restaurant GUID"},
                "start_date": {"type": "string", "description": "Start datetime in ISO 8601 format"},
                "end_date": {"type": "string", "description": "End datetime in ISO 8601 format"},
                "page": {"type": "integer", "description": "Page number"},
                "page_size": {"type": "integer", "description": "Orders per page"},
            },
            "required": ["restaurant_guid"],
        },
    },
    {
        "name": "toast_get_order",
        "description": "Get detailed information about a specific Toast order",
        "parameters": {
            "type": "object",
            "properties": {
                "restaurant_guid": {"type": "string", "description": "Restaurant GUID"},
                "order_guid": {"type": "string", "description": "Order GUID"},
            },
            "required": ["restaurant_guid", "order_guid"],
        },
    },
    {
        "name": "toast_list_menu_items",
        "description": "List menu items and their prices for a Toast restaurant",
        "parameters": {
            "type": "object",
            "properties": {
                "restaurant_guid": {"type": "string", "description": "Restaurant GUID"},
                "last_modified": {"type": "string", "description": "Return items modified after this ISO datetime"},
            },
            "required": ["restaurant_guid"],
        },
    },
    {
        "name": "toast_get_payment_report",
        "description": "Get payment and revenue summary for a restaurant over a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "restaurant_guid": {"type": "string", "description": "Restaurant GUID"},
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["restaurant_guid", "start_date", "end_date"],
        },
    },
    {
        "name": "toast_list_employees",
        "description": "List employees and their roles for a Toast restaurant",
        "parameters": {
            "type": "object",
            "properties": {
                "restaurant_guid": {"type": "string", "description": "Restaurant GUID"},
                "deleted": {"type": "boolean", "description": "Include deleted employees"},
            },
            "required": ["restaurant_guid"],
        },
    },
]


async def _get_token(client: httpx.AsyncClient) -> str:
    r = await client.post(
        AUTH_URL,
        json={
            "clientId": os.getenv("TOAST_CLIENT_ID", ""),
            "clientSecret": os.getenv("TOAST_CLIENT_SECRET", ""),
            "userAccessType": "TOAST_MACHINE_CLIENT",
        },
    )
    r.raise_for_status()
    return r.json().get("token", {}).get("accessToken", "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    client_id = os.getenv("TOAST_CLIENT_ID", "")
    client_secret = os.getenv("TOAST_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return {"error": "TOAST_CLIENT_ID and TOAST_CLIENT_SECRET not configured"}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            token = await _get_token(client)
            headers = {
                "Authorization": f"Bearer {token}",
                "Toast-Restaurant-External-ID": arguments.get("restaurant_guid", ""),
            }

            if tool_name == "toast_list_restaurants":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/partners/v1/restaurants", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "toast_list_orders":
                rest_guid = arguments["restaurant_guid"]
                params: dict[str, Any] = {}
                if "start_date" in arguments:
                    params["startDate"] = arguments["start_date"]
                if "end_date" in arguments:
                    params["endDate"] = arguments["end_date"]
                if "page" in arguments:
                    params["page"] = arguments["page"]
                if "page_size" in arguments:
                    params["pageSize"] = arguments["page_size"]
                headers["Toast-Restaurant-External-ID"] = rest_guid
                r = await client.get(f"{BASE_URL}/orders/v2/orders", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "toast_get_order":
                headers["Toast-Restaurant-External-ID"] = arguments["restaurant_guid"]
                r = await client.get(
                    f"{BASE_URL}/orders/v2/orders/{arguments['order_guid']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "toast_list_menu_items":
                rest_guid = arguments["restaurant_guid"]
                params = {}
                if "last_modified" in arguments:
                    params["lastModified"] = arguments["last_modified"]
                headers["Toast-Restaurant-External-ID"] = rest_guid
                r = await client.get(f"{BASE_URL}/config/v2/menus", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "toast_get_payment_report":
                rest_guid = arguments["restaurant_guid"]
                headers["Toast-Restaurant-External-ID"] = rest_guid
                r = await client.get(
                    f"{BASE_URL}/orders/v2/ordersBulk",
                    headers=headers,
                    params={
                        "startDate": arguments["start_date"],
                        "endDate": arguments["end_date"],
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "toast_list_employees":
                rest_guid = arguments["restaurant_guid"]
                headers["Toast-Restaurant-External-ID"] = rest_guid
                params = {}
                if "deleted" in arguments:
                    params["deleted"] = str(arguments["deleted"]).lower()
                r = await client.get(f"{BASE_URL}/labor/v1/employees", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
