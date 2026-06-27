"""PayPal MCP server — orders, payouts, and transaction reporting.

Environment variables:
  PAYPAL_CLIENT_ID:     PayPal app client ID
  PAYPAL_CLIENT_SECRET: PayPal app client secret
  PAYPAL_SANDBOX:       'true' (default) or 'false' for live
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "paypal_create_order",
        "description": "Create a PayPal checkout order",
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "Order total amount"},
                "currency": {"type": "string", "default": "USD"},
                "intent": {"type": "string", "enum": ["CAPTURE", "AUTHORIZE"], "default": "CAPTURE"},
                "description": {"type": "string"},
                "return_url": {"type": "string"},
                "cancel_url": {"type": "string"},
            },
            "required": ["amount"],
        },
    },
    {
        "name": "paypal_capture_order",
        "description": "Capture payment for an approved PayPal order",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "paypal_get_order",
        "description": "Get details of a PayPal order",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "paypal_create_payout",
        "description": "Send a payout to one or more PayPal recipients",
        "parameters": {
            "type": "object",
            "properties": {
                "sender_batch_id": {"type": "string", "description": "Unique ID for this payout batch"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "receiver": {"type": "string", "description": "Recipient email or PayPal ID"},
                            "amount": {"type": "number"},
                            "currency": {"type": "string", "default": "USD"},
                            "note": {"type": "string"},
                        },
                        "required": ["receiver", "amount"],
                    },
                },
                "email_subject": {"type": "string"},
                "email_message": {"type": "string"},
            },
            "required": ["sender_batch_id", "items"],
        },
    },
    {
        "name": "paypal_list_transactions",
        "description": "List transactions for a date range using the Reporting API",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "ISO8601 start date-time e.g. 2024-01-01T00:00:00-0700"},
                "end_date": {"type": "string", "description": "ISO8601 end date-time"},
                "page_size": {"type": "integer", "default": 100},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "paypal_get_balance",
        "description": "Get current PayPal account balance",
        "parameters": {
            "type": "object",
            "properties": {
                "currency": {"type": "string", "description": "Currency code filter (optional)"},
            },
        },
    },
    {
        "name": "paypal_show_payout_batch",
        "description": "Get status of a payout batch",
        "parameters": {
            "type": "object",
            "properties": {
                "payout_batch_id": {"type": "string"},
            },
            "required": ["payout_batch_id"],
        },
    },
]


def _base_url() -> str:
    sandbox = os.getenv("PAYPAL_SANDBOX", "true").lower() == "true"
    return "https://api-m.sandbox.paypal.com" if sandbox else "https://api-m.paypal.com"


async def _get_token() -> str:
    base = _base_url()
    client_id = os.getenv("PAYPAL_CLIENT_ID", "")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return ""
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{base}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
        )
        if r.status_code == 200:
            return r.json().get("access_token", "")
    return ""


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not os.getenv("PAYPAL_CLIENT_ID") or not os.getenv("PAYPAL_CLIENT_SECRET"):
        return {"error": "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET required"}

    token = await _get_token()
    if not token:
        return {"error": "Failed to obtain PayPal access token"}

    base = _base_url()
    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "paypal_create_order":
                body: dict[str, Any] = {
                    "intent": arguments.get("intent", "CAPTURE"),
                    "purchase_units": [
                        {
                            "amount": {
                                "currency_code": arguments.get("currency", "USD"),
                                "value": str(arguments["amount"]),
                            },
                            "description": arguments.get("description", ""),
                        }
                    ],
                }
                if return_url := arguments.get("return_url"):
                    body["application_context"] = {
                        "return_url": return_url,
                        "cancel_url": arguments.get("cancel_url", return_url),
                    }
                r = await c.post(f"{base}/v2/checkout/orders", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "paypal_capture_order":
                order_id = arguments["order_id"]
                r = await c.post(
                    f"{base}/v2/checkout/orders/{order_id}/capture",
                    headers=hdrs,
                    json={},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "paypal_get_order":
                order_id = arguments["order_id"]
                r = await c.get(f"{base}/v2/checkout/orders/{order_id}", headers=hdrs)
                r.raise_for_status()
                return r.json()

            elif tool_name == "paypal_create_payout":
                items = [
                    {
                        "recipient_type": "EMAIL",
                        "receiver": item["receiver"],
                        "amount": {
                            "value": str(item["amount"]),
                            "currency": item.get("currency", "USD"),
                        },
                        "note": item.get("note", ""),
                    }
                    for item in arguments["items"]
                ]
                body = {
                    "sender_batch_header": {
                        "sender_batch_id": arguments["sender_batch_id"],
                        "email_subject": arguments.get("email_subject", "You have a payment"),
                        "email_message": arguments.get("email_message", ""),
                    },
                    "items": items,
                }
                r = await c.post(f"{base}/v1/payments/payouts", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "paypal_list_transactions":
                params = {
                    "start_date": arguments["start_date"],
                    "end_date": arguments["end_date"],
                    "page_size": arguments.get("page_size", 100),
                    "page": arguments.get("page", 1),
                }
                r = await c.get(
                    f"{base}/v1/reporting/transactions",
                    headers=hdrs,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "paypal_get_balance":
                params = {}
                if currency := arguments.get("currency"):
                    params["currency_code"] = currency
                r = await c.get(f"{base}/v1/reporting/balances", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "paypal_show_payout_batch":
                batch_id = arguments["payout_batch_id"]
                r = await c.get(f"{base}/v1/payments/payouts/{batch_id}", headers=hdrs)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("paypal_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
