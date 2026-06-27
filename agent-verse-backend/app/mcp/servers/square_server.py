"""Square MCP server — payments, customers, catalog, and orders.

Environment variables:
  SQUARE_ACCESS_TOKEN: Square OAuth2 access token or personal access token
  SQUARE_SANDBOX:      'true' (default) or 'false' for production
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "square_list_customers",
        "description": "List Square customers",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "cursor": {"type": "string", "description": "Pagination cursor"},
                "sort_field": {"type": "string", "enum": ["DEFAULT", "CREATED_AT"], "default": "DEFAULT"},
            },
        },
    },
    {
        "name": "square_create_customer",
        "description": "Create a new Square customer",
        "parameters": {
            "type": "object",
            "properties": {
                "given_name": {"type": "string"},
                "family_name": {"type": "string"},
                "email_address": {"type": "string"},
                "phone_number": {"type": "string"},
                "reference_id": {"type": "string"},
                "note": {"type": "string"},
            },
        },
    },
    {
        "name": "square_create_payment",
        "description": "Create a Square payment (charge a card)",
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "Payment source (card nonce or card-on-file ID)"},
                "amount": {"type": "integer", "description": "Amount in smallest currency unit (cents for USD)"},
                "currency": {"type": "string", "default": "USD"},
                "customer_id": {"type": "string"},
                "note": {"type": "string"},
                "reference_id": {"type": "string"},
            },
            "required": ["source_id", "amount"],
        },
    },
    {
        "name": "square_list_payments",
        "description": "List Square payments with optional date range filter",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "cursor": {"type": "string"},
                "begin_time": {"type": "string", "description": "RFC3339 start time"},
                "end_time": {"type": "string", "description": "RFC3339 end time"},
            },
        },
    },
    {
        "name": "square_create_refund",
        "description": "Refund a Square payment",
        "parameters": {
            "type": "object",
            "properties": {
                "payment_id": {"type": "string"},
                "amount": {"type": "integer", "description": "Amount to refund; omit for full refund"},
                "currency": {"type": "string", "default": "USD"},
                "reason": {"type": "string"},
            },
            "required": ["payment_id"],
        },
    },
    {
        "name": "square_list_catalog",
        "description": "List Square catalog items (products/services)",
        "parameters": {
            "type": "object",
            "properties": {
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Catalog object types e.g. ['ITEM', 'ITEM_VARIATION', 'CATEGORY']",
                    "default": ["ITEM"],
                },
                "cursor": {"type": "string"},
            },
        },
    },
    {
        "name": "square_create_order",
        "description": "Create a new Square order",
        "parameters": {
            "type": "object",
            "properties": {
                "location_id": {"type": "string"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "quantity": {"type": "string"},
                            "base_price_money": {
                                "type": "object",
                                "properties": {
                                    "amount": {"type": "integer"},
                                    "currency": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "customer_id": {"type": "string"},
            },
            "required": ["location_id", "line_items"],
        },
    },
    {
        "name": "square_list_locations",
        "description": "List Square business locations",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


def _base_url() -> str:
    sandbox = os.getenv("SQUARE_SANDBOX", "true").lower() == "true"
    return "https://connect.squareupsandbox.com" if sandbox else "https://connect.squareup.com"


def _headers() -> dict[str, str]:
    token = os.getenv("SQUARE_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Square-Version": "2024-01-17",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("SQUARE_ACCESS_TOKEN"):
        return {"error": "SQUARE_ACCESS_TOKEN not configured"}

    base = _base_url()
    hdrs = _headers()

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "square_list_customers":
                params: dict[str, Any] = {"limit": arguments.get("limit", 20)}
                if cursor := arguments.get("cursor"):
                    params["cursor"] = cursor
                r = await c.get(f"{base}/v2/customers", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "square_create_customer":
                body = {k: v for k, v in arguments.items() if v is not None}
                body["idempotency_key"] = str(uuid.uuid4())
                r = await c.post(f"{base}/v2/customers", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "square_create_payment":
                body: dict[str, Any] = {
                    "source_id": arguments["source_id"],
                    "idempotency_key": str(uuid.uuid4()),
                    "amount_money": {
                        "amount": arguments["amount"],
                        "currency": arguments.get("currency", "USD"),
                    },
                }
                for opt in ("customer_id", "note", "reference_id"):
                    if v := arguments.get(opt):
                        body[opt] = v
                r = await c.post(f"{base}/v2/payments", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "square_list_payments":
                params = {"limit": arguments.get("limit", 20)}
                for f in ("cursor", "begin_time", "end_time"):
                    if v := arguments.get(f):
                        params[f] = v
                r = await c.get(f"{base}/v2/payments", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "square_create_refund":
                payment_id = arguments["payment_id"]
                body = {"idempotency_key": str(uuid.uuid4()), "payment_id": payment_id}
                if amount := arguments.get("amount"):
                    body["amount_money"] = {
                        "amount": amount,
                        "currency": arguments.get("currency", "USD"),
                    }
                if reason := arguments.get("reason"):
                    body["reason"] = reason
                r = await c.post(f"{base}/v2/refunds", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "square_list_catalog":
                params = {}
                if types := arguments.get("types", ["ITEM"]):
                    params["types"] = ",".join(types)
                if cursor := arguments.get("cursor"):
                    params["cursor"] = cursor
                r = await c.get(f"{base}/v2/catalog/list", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "square_create_order":
                body = {
                    "idempotency_key": str(uuid.uuid4()),
                    "order": {
                        "location_id": arguments["location_id"],
                        "line_items": arguments["line_items"],
                    },
                }
                if cid := arguments.get("customer_id"):
                    body["order"]["customer_id"] = cid
                r = await c.post(f"{base}/v2/orders", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "square_list_locations":
                r = await c.get(f"{base}/v2/locations", headers=hdrs)
                r.raise_for_status()
                data = r.json()
                return {
                    "locations": [
                        {"id": loc["id"], "name": loc.get("name", ""),
                         "status": loc.get("status", ""), "country": loc.get("country", "")}
                        for loc in data.get("locations", [])
                    ]
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("square_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
