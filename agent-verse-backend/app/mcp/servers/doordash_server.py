"""DoorDash Drive MCP server — on-demand delivery creation and management.

Environment:
  DOORDASH_DEVELOPER_ID: DoorDash developer ID
  DOORDASH_KEY_ID: DoorDash API key ID
  DOORDASH_SIGNING_SECRET: DoorDash JWT signing secret
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://openapi.doordash.com"

TOOL_DEFINITIONS = [
    {
        "name": "doordash_create_delivery",
        "description": "Create a new DoorDash Drive on-demand delivery request",
        "parameters": {
            "type": "object",
            "properties": {
                "external_delivery_id": {"type": "string", "description": "Your unique ID for this delivery"},
                "pickup_address": {"type": "string", "description": "Full pickup address"},
                "pickup_business_name": {"type": "string", "description": "Business name at pickup location"},
                "pickup_phone_number": {"type": "string", "description": "Pickup location phone"},
                "dropoff_address": {"type": "string", "description": "Full dropoff address"},
                "dropoff_contact_given_name": {"type": "string", "description": "Recipient first name"},
                "dropoff_phone_number": {"type": "string", "description": "Recipient phone number"},
                "order_value": {"type": "integer", "description": "Order value in cents"},
                "currency": {"type": "string", "description": "Currency code (e.g. USD)"},
            },
            "required": ["external_delivery_id", "pickup_address", "dropoff_address"],
        },
    },
    {
        "name": "doordash_get_delivery",
        "description": "Get the current status and details of a DoorDash delivery",
        "parameters": {
            "type": "object",
            "properties": {
                "external_delivery_id": {"type": "string", "description": "Your external delivery ID"},
            },
            "required": ["external_delivery_id"],
        },
    },
    {
        "name": "doordash_cancel_delivery",
        "description": "Cancel an active DoorDash delivery that has not yet been picked up",
        "parameters": {
            "type": "object",
            "properties": {
                "external_delivery_id": {"type": "string", "description": "Your external delivery ID to cancel"},
            },
            "required": ["external_delivery_id"],
        },
    },
    {
        "name": "doordash_list_deliveries",
        "description": "List recent DoorDash deliveries with optional status filter",
        "parameters": {
            "type": "object",
            "properties": {
                "external_business_id": {"type": "string", "description": "Filter by business ID"},
                "limit": {"type": "integer", "description": "Maximum deliveries to return"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "doordash_get_delivery_quote",
        "description": "Get a price quote for a delivery before creating it",
        "parameters": {
            "type": "object",
            "properties": {
                "pickup_address": {"type": "string", "description": "Pickup address"},
                "dropoff_address": {"type": "string", "description": "Dropoff address"},
                "order_value": {"type": "integer", "description": "Order value in cents for fee calculation"},
            },
            "required": ["pickup_address", "dropoff_address"],
        },
    },
    {
        "name": "doordash_update_tip",
        "description": "Update the tip amount for a DoorDash delivery",
        "parameters": {
            "type": "object",
            "properties": {
                "external_delivery_id": {"type": "string", "description": "Your external delivery ID"},
                "tip": {"type": "integer", "description": "Tip amount in cents"},
            },
            "required": ["external_delivery_id", "tip"],
        },
    },
]


def _make_jwt() -> str:
    """Create JWT for DoorDash authentication."""
    try:
        import jwt as pyjwt
        developer_id = os.getenv("DOORDASH_DEVELOPER_ID", "")
        key_id = os.getenv("DOORDASH_KEY_ID", "")
        signing_secret = os.getenv("DOORDASH_SIGNING_SECRET", "")
        payload = {
            "aud": "doordash",
            "iss": developer_id,
            "kid": key_id,
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        }
        return pyjwt.encode(payload, signing_secret, algorithm="HS256")
    except Exception:
        return ""


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    developer_id = os.getenv("DOORDASH_DEVELOPER_ID", "")
    key_id = os.getenv("DOORDASH_KEY_ID", "")
    signing_secret = os.getenv("DOORDASH_SIGNING_SECRET", "")
    if not developer_id or not key_id or not signing_secret:
        return {"error": "DOORDASH_DEVELOPER_ID, DOORDASH_KEY_ID, and DOORDASH_SIGNING_SECRET not configured"}

    token = _make_jwt()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "doordash_create_delivery":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/drive/v2/deliveries", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "doordash_get_delivery":
                r = await client.get(
                    f"{BASE_URL}/drive/v2/deliveries/{arguments['external_delivery_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "doordash_cancel_delivery":
                r = await client.put(
                    f"{BASE_URL}/drive/v2/deliveries/{arguments['external_delivery_id']}/cancel",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "doordash_list_deliveries":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/drive/v2/deliveries", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "doordash_get_delivery_quote":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/drive/v2/deliveries/quote", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "doordash_update_tip":
                r = await client.patch(
                    f"{BASE_URL}/drive/v2/deliveries/{arguments['external_delivery_id']}",
                    headers=headers,
                    json={"tip": arguments["tip"]},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
