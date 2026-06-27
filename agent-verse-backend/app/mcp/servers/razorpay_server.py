"""Razorpay MCP server — payments, orders, and payouts via Razorpay API.

Environment variables:
  RAZORPAY_KEY_ID:     Razorpay Key ID
  RAZORPAY_KEY_SECRET: Razorpay Key Secret
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

RAZORPAY_BASE = "https://api.razorpay.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "razorpay_create_order",
        "description": "Create a Razorpay payment order",
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Amount in smallest currency unit (paise for INR)"},
                "currency": {"type": "string", "default": "INR"},
                "receipt": {"type": "string", "description": "Order receipt ID (max 40 chars)"},
                "notes": {"type": "object", "description": "Key-value metadata"},
                "partial_payment": {"type": "boolean", "default": False},
            },
            "required": ["amount"],
        },
    },
    {
        "name": "razorpay_get_order",
        "description": "Fetch a Razorpay order by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "razorpay_list_payments",
        "description": "List Razorpay payments with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 10},
                "skip": {"type": "integer", "default": 0},
                "from": {"type": "integer", "description": "Unix timestamp lower bound"},
                "to": {"type": "integer", "description": "Unix timestamp upper bound"},
            },
        },
    },
    {
        "name": "razorpay_capture_payment",
        "description": "Capture an authorized Razorpay payment",
        "parameters": {
            "type": "object",
            "properties": {
                "payment_id": {"type": "string"},
                "amount": {"type": "integer", "description": "Amount to capture in paise"},
                "currency": {"type": "string", "default": "INR"},
            },
            "required": ["payment_id", "amount"],
        },
    },
    {
        "name": "razorpay_create_refund",
        "description": "Issue a refund for a Razorpay payment",
        "parameters": {
            "type": "object",
            "properties": {
                "payment_id": {"type": "string"},
                "amount": {"type": "integer", "description": "Amount to refund in paise; omit for full refund"},
                "speed": {"type": "string", "enum": ["normal", "optimum"], "default": "optimum"},
                "notes": {"type": "object"},
            },
            "required": ["payment_id"],
        },
    },
    {
        "name": "razorpay_create_payout",
        "description": "Initiate a Razorpay payout (requires X account)",
        "parameters": {
            "type": "object",
            "properties": {
                "account_number": {"type": "string", "description": "Razorpay X account number"},
                "fund_account_id": {"type": "string"},
                "amount": {"type": "integer", "description": "Amount in paise"},
                "currency": {"type": "string", "default": "INR"},
                "mode": {"type": "string", "enum": ["NEFT", "RTGS", "IMPS", "IFT", "UPI"], "default": "IMPS"},
                "purpose": {"type": "string", "default": "payout"},
                "narration": {"type": "string"},
            },
            "required": ["account_number", "fund_account_id", "amount"],
        },
    },
    {
        "name": "razorpay_list_customers",
        "description": "List Razorpay customers",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 10},
                "skip": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "razorpay_create_customer",
        "description": "Create a new Razorpay customer",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "contact": {"type": "string", "description": "Phone number with country code"},
                "notes": {"type": "object"},
            },
            "required": ["name"],
        },
    },
]


def _auth() -> tuple[str, str]:
    return (
        os.getenv("RAZORPAY_KEY_ID", ""),
        os.getenv("RAZORPAY_KEY_SECRET", ""),
    )


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key_id, key_secret = _auth()
    if not key_id or not key_secret:
        return {"error": "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET required"}

    hdrs = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(
            timeout=30.0, auth=(key_id, key_secret)
        ) as c:
            if tool_name == "razorpay_create_order":
                body: dict[str, Any] = {
                    "amount": arguments["amount"],
                    "currency": arguments.get("currency", "INR"),
                    "partial_payment": arguments.get("partial_payment", False),
                }
                if receipt := arguments.get("receipt"):
                    body["receipt"] = receipt
                if notes := arguments.get("notes"):
                    body["notes"] = notes
                r = await c.post(f"{RAZORPAY_BASE}/orders", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "razorpay_get_order":
                order_id = arguments["order_id"]
                r = await c.get(f"{RAZORPAY_BASE}/orders/{order_id}", headers=hdrs)
                r.raise_for_status()
                return r.json()

            elif tool_name == "razorpay_list_payments":
                params: dict[str, Any] = {
                    "count": arguments.get("count", 10),
                    "skip": arguments.get("skip", 0),
                }
                for f in ("from", "to"):
                    if v := arguments.get(f):
                        params[f] = v
                r = await c.get(f"{RAZORPAY_BASE}/payments", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "razorpay_capture_payment":
                pid = arguments["payment_id"]
                body = {
                    "amount": arguments["amount"],
                    "currency": arguments.get("currency", "INR"),
                }
                r = await c.post(
                    f"{RAZORPAY_BASE}/payments/{pid}/capture",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "razorpay_create_refund":
                pid = arguments["payment_id"]
                body = {"speed": arguments.get("speed", "optimum")}
                if amount := arguments.get("amount"):
                    body["amount"] = amount
                if notes := arguments.get("notes"):
                    body["notes"] = notes
                r = await c.post(
                    f"{RAZORPAY_BASE}/payments/{pid}/refund",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "razorpay_create_payout":
                body = {
                    "account_number": arguments["account_number"],
                    "fund_account_id": arguments["fund_account_id"],
                    "amount": arguments["amount"],
                    "currency": arguments.get("currency", "INR"),
                    "mode": arguments.get("mode", "IMPS"),
                    "purpose": arguments.get("purpose", "payout"),
                    "queue_if_low_balance": True,
                }
                if narration := arguments.get("narration"):
                    body["narration"] = narration
                r = await c.post(f"{RAZORPAY_BASE}/payouts", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "razorpay_list_customers":
                params = {
                    "count": arguments.get("count", 10),
                    "skip": arguments.get("skip", 0),
                }
                r = await c.get(f"{RAZORPAY_BASE}/customers", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "razorpay_create_customer":
                body = {k: v for k, v in arguments.items() if v is not None}
                r = await c.post(f"{RAZORPAY_BASE}/customers", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("razorpay_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
