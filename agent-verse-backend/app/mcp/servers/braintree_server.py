"""Braintree MCP server — payment transactions, customers, and subscriptions.

Environment:
  BRAINTREE_MERCHANT_ID: Braintree merchant ID
  BRAINTREE_PUBLIC_KEY:  Braintree public key
  BRAINTREE_PRIVATE_KEY: Braintree private key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.braintreegateway.com"

TOOL_DEFINITIONS = [
    {
        "name": "braintree_create_transaction",
        "description": "Create a payment transaction",
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "string", "description": "Decimal amount string, e.g. '10.00'"},
                "payment_method_nonce": {"type": "string", "description": "Braintree payment method nonce"},
                "customer_id": {"type": "string"},
                "order_id": {"type": "string"},
                "submit_for_settlement": {"type": "boolean", "default": True},
            },
            "required": ["amount", "payment_method_nonce"],
        },
    },
    {
        "name": "braintree_find_transaction",
        "description": "Find a transaction by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string"},
            },
            "required": ["transaction_id"],
        },
    },
    {
        "name": "braintree_list_transactions",
        "description": "Search and list transactions with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "status": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "e.g. ['authorized', 'settled', 'failed']",
                },
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "braintree_create_customer",
        "description": "Create a new Braintree customer",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "company": {"type": "string"},
                "payment_method_nonce": {"type": "string"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "braintree_find_customer",
        "description": "Find a Braintree customer by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "braintree_create_subscription",
        "description": "Create a subscription for a customer payment method",
        "parameters": {
            "type": "object",
            "properties": {
                "payment_method_token": {"type": "string"},
                "plan_id": {"type": "string"},
                "first_billing_date": {"type": "string", "description": "YYYY-MM-DD"},
                "price": {"type": "string", "description": "Override subscription price"},
            },
            "required": ["payment_method_token", "plan_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    merchant_id = os.getenv("BRAINTREE_MERCHANT_ID", "")
    public_key = os.getenv("BRAINTREE_PUBLIC_KEY", "")
    private_key = os.getenv("BRAINTREE_PRIVATE_KEY", "")
    if not merchant_id or not public_key or not private_key:
        return {"error": "BRAINTREE_MERCHANT_ID, BRAINTREE_PUBLIC_KEY, and BRAINTREE_PRIVATE_KEY must be configured"}

    auth = (public_key, private_key)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-ApiVersion": "6",
    }

    try:
        async with httpx.AsyncClient(auth=auth, headers=headers, timeout=30.0) as c:
            if tool_name == "braintree_create_transaction":
                payload: dict[str, Any] = {
                    "transaction": {
                        "amount": arguments["amount"],
                        "paymentMethodNonce": arguments["payment_method_nonce"],
                        "options": {"submitForSettlement": arguments.get("submit_for_settlement", True)},
                    }
                }
                if cid := arguments.get("customer_id"):
                    payload["transaction"]["customerId"] = cid
                if oid := arguments.get("order_id"):
                    payload["transaction"]["orderId"] = oid
                r = await c.post(f"{BASE}/merchants/{merchant_id}/transactions", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "braintree_find_transaction":
                tid = arguments["transaction_id"]
                r = await c.get(f"{BASE}/merchants/{merchant_id}/transactions/{tid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "braintree_list_transactions":
                search: dict[str, Any] = {}
                if cid := arguments.get("customer_id"):
                    search["customer_id"] = {"is": cid}
                if statuses := arguments.get("status"):
                    search["status"] = {"in": statuses}
                r = await c.post(
                    f"{BASE}/merchants/{merchant_id}/transactions/advanced_search",
                    json={"search": search},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "braintree_create_customer":
                customer: dict[str, Any] = {"email": arguments["email"]}
                for field in ("first_name", "last_name", "phone", "company"):
                    mapped = {"first_name": "firstName", "last_name": "lastName"}.get(field, field)
                    if v := arguments.get(field):
                        customer[mapped] = v
                if nonce := arguments.get("payment_method_nonce"):
                    customer["paymentMethodNonce"] = nonce
                r = await c.post(
                    f"{BASE}/merchants/{merchant_id}/customers",
                    json={"customer": customer},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "braintree_find_customer":
                cid = arguments["customer_id"]
                r = await c.get(f"{BASE}/merchants/{merchant_id}/customers/{cid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "braintree_create_subscription":
                sub: dict[str, Any] = {
                    "paymentMethodToken": arguments["payment_method_token"],
                    "planId": arguments["plan_id"],
                }
                if fbd := arguments.get("first_billing_date"):
                    sub["firstBillingDate"] = fbd
                if price := arguments.get("price"):
                    sub["price"] = price
                r = await c.post(
                    f"{BASE}/merchants/{merchant_id}/subscriptions",
                    json={"subscription": sub},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("braintree_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
