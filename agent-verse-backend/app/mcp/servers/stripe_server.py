"""Stripe MCP server — comprehensive payments, subscriptions, and billing.

Environment variables:
  STRIPE_SECRET_KEY: Stripe secret API key (sk_live_... or sk_test_...)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

STRIPE_BASE = "https://api.stripe.com/v1"

TOOL_DEFINITIONS = [
    # Customers
    {
        "name": "stripe_list_customers",
        "description": "List Stripe customers with optional email filter",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "starting_after": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "stripe_create_customer",
        "description": "Create a new Stripe customer",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "description": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "stripe_get_customer",
        "description": "Retrieve a Stripe customer by ID",
        "parameters": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    # Payment Intents
    {
        "name": "stripe_create_payment_intent",
        "description": "Create a PaymentIntent to charge a customer",
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Amount in smallest currency unit (e.g. cents)"},
                "currency": {"type": "string", "default": "usd"},
                "customer": {"type": "string", "description": "Stripe customer ID"},
                "payment_method": {"type": "string"},
                "description": {"type": "string"},
                "metadata": {"type": "object"},
                "confirm": {"type": "boolean", "default": False},
                "return_url": {"type": "string"},
            },
            "required": ["amount", "currency"],
        },
    },
    {
        "name": "stripe_confirm_payment_intent",
        "description": "Confirm a PaymentIntent to trigger payment capture",
        "parameters": {
            "type": "object",
            "properties": {
                "payment_intent_id": {"type": "string"},
                "payment_method": {"type": "string"},
                "return_url": {"type": "string"},
            },
            "required": ["payment_intent_id"],
        },
    },
    {
        "name": "stripe_retrieve_payment_intent",
        "description": "Retrieve a PaymentIntent by ID",
        "parameters": {
            "type": "object",
            "properties": {"payment_intent_id": {"type": "string"}},
            "required": ["payment_intent_id"],
        },
    },
    # Subscriptions
    {
        "name": "stripe_list_subscriptions",
        "description": "List subscriptions with optional customer and status filters",
        "parameters": {
            "type": "object",
            "properties": {
                "customer": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["active", "past_due", "canceled", "trialing", "all"],
                    "default": "active",
                },
                "limit": {"type": "integer", "default": 10},
                "starting_after": {"type": "string"},
            },
        },
    },
    {
        "name": "stripe_create_subscription",
        "description": "Create a subscription for a customer",
        "parameters": {
            "type": "object",
            "properties": {
                "customer": {"type": "string"},
                "price_id": {"type": "string"},
                "trial_period_days": {"type": "integer"},
                "metadata": {"type": "object"},
            },
            "required": ["customer", "price_id"],
        },
    },
    {
        "name": "stripe_cancel_subscription",
        "description": "Cancel an active subscription",
        "parameters": {
            "type": "object",
            "properties": {
                "subscription_id": {"type": "string"},
                "cancel_at_period_end": {"type": "boolean", "default": False},
            },
            "required": ["subscription_id"],
        },
    },
    # Refunds
    {
        "name": "stripe_create_refund",
        "description": "Issue a refund for a charge or PaymentIntent",
        "parameters": {
            "type": "object",
            "properties": {
                "payment_intent": {"type": "string"},
                "charge": {"type": "string"},
                "amount": {"type": "integer", "description": "Amount to refund in smallest currency unit (omit for full)"},
                "reason": {
                    "type": "string",
                    "enum": ["duplicate", "fraudulent", "requested_by_customer"],
                },
            },
        },
    },
    # Invoices
    {
        "name": "stripe_list_invoices",
        "description": "List invoices with optional customer filter",
        "parameters": {
            "type": "object",
            "properties": {
                "customer": {"type": "string"},
                "status": {"type": "string", "enum": ["draft", "open", "paid", "void", "uncollectible"]},
                "limit": {"type": "integer", "default": 10},
                "starting_after": {"type": "string"},
            },
        },
    },
    {
        "name": "stripe_create_invoice",
        "description": "Create a draft invoice for a customer",
        "parameters": {
            "type": "object",
            "properties": {
                "customer": {"type": "string"},
                "collection_method": {
                    "type": "string",
                    "enum": ["charge_automatically", "send_invoice"],
                    "default": "charge_automatically",
                },
                "days_until_due": {"type": "integer"},
                "description": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["customer"],
        },
    },
    # Products
    {
        "name": "stripe_list_products",
        "description": "List Stripe products",
        "parameters": {
            "type": "object",
            "properties": {
                "active": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 10},
                "starting_after": {"type": "string"},
            },
        },
    },
    {
        "name": "stripe_create_product",
        "description": "Create a new Stripe product",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "metadata": {"type": "object"},
                "images": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name"],
        },
    },
    # Prices
    {
        "name": "stripe_list_prices",
        "description": "List prices for a product or all prices",
        "parameters": {
            "type": "object",
            "properties": {
                "product": {"type": "string"},
                "active": {"type": "boolean"},
                "type": {"type": "string", "enum": ["one_time", "recurring"]},
                "limit": {"type": "integer", "default": 10},
                "starting_after": {"type": "string"},
            },
        },
    },
    {
        "name": "stripe_create_price",
        "description": "Create a price for a product",
        "parameters": {
            "type": "object",
            "properties": {
                "product": {"type": "string"},
                "unit_amount": {"type": "integer", "description": "Price in smallest currency unit"},
                "currency": {"type": "string", "default": "usd"},
                "recurring": {
                    "type": "object",
                    "description": "For subscription prices: {interval: 'month'|'year'|'week'|'day', interval_count: 1}",
                },
                "nickname": {"type": "string"},
            },
            "required": ["product", "unit_amount", "currency"],
        },
    },
    # Charges
    {
        "name": "stripe_list_charges",
        "description": "List charges with optional customer and date range filters",
        "parameters": {
            "type": "object",
            "properties": {
                "customer": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "starting_after": {"type": "string"},
                "created_gte": {"type": "integer", "description": "Unix timestamp lower bound"},
                "created_lte": {"type": "integer", "description": "Unix timestamp upper bound"},
            },
        },
    },
    # Payouts
    {
        "name": "stripe_create_payout",
        "description": "Create a payout to the bank account on file (platform accounts only)",
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer"},
                "currency": {"type": "string", "default": "usd"},
                "description": {"type": "string"},
            },
            "required": ["amount", "currency"],
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("STRIPE_SECRET_KEY", "")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/x-www-form-urlencoded"}


def _flatten(d: dict[str, Any], parent_key: str = "", sep: str = "[") -> dict[str, str]:
    """Flatten nested dict to Stripe-style form encoding."""
    items: list[tuple[str, str]] = []
    for k, v in d.items():
        new_key = f"{parent_key}[{k}]" if parent_key else k
        if isinstance(v, dict):
            items.extend(_flatten(v, new_key, sep).items())
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    items.extend(_flatten(item, f"{new_key}[{i}]", sep).items())
                else:
                    items.append((f"{new_key}[]", str(item)))
        elif v is not None:
            items.append((new_key, str(v).lower() if isinstance(v, bool) else str(v)))
    return dict(items)


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        return {"error": "STRIPE_SECRET_KEY not configured"}

    hdrs = {"Authorization": f"Bearer {key}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "stripe_list_customers":
                params: dict[str, Any] = {"limit": arguments.get("limit", 10)}
                if email := arguments.get("email"):
                    params["email"] = email
                if sa := arguments.get("starting_after"):
                    params["starting_after"] = sa
                r = await c.get(f"{STRIPE_BASE}/customers", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_create_customer":
                data = _flatten({k: v for k, v in arguments.items() if v is not None})
                r = await c.post(f"{STRIPE_BASE}/customers", headers=hdrs, data=data)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_get_customer":
                cid = arguments["customer_id"]
                r = await c.get(f"{STRIPE_BASE}/customers/{cid}", headers=hdrs)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_create_payment_intent":
                data = _flatten({k: v for k, v in arguments.items() if v is not None})
                r = await c.post(f"{STRIPE_BASE}/payment_intents", headers=hdrs, data=data)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_confirm_payment_intent":
                pi_id = arguments["payment_intent_id"]
                data = _flatten({k: v for k, v in arguments.items()
                                 if k != "payment_intent_id" and v is not None})
                r = await c.post(
                    f"{STRIPE_BASE}/payment_intents/{pi_id}/confirm",
                    headers=hdrs,
                    data=data or {},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_retrieve_payment_intent":
                pi_id = arguments["payment_intent_id"]
                r = await c.get(f"{STRIPE_BASE}/payment_intents/{pi_id}", headers=hdrs)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_list_subscriptions":
                params = {"limit": arguments.get("limit", 10)}
                for field in ("customer", "status", "starting_after"):
                    if v := arguments.get(field):
                        params[field] = v
                r = await c.get(f"{STRIPE_BASE}/subscriptions", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_create_subscription":
                data = {
                    "customer": arguments["customer"],
                    "items[0][price]": arguments["price_id"],
                }
                if tpd := arguments.get("trial_period_days"):
                    data["trial_period_days"] = str(tpd)
                if meta := arguments.get("metadata"):
                    data.update(_flatten(meta, "metadata"))
                r = await c.post(f"{STRIPE_BASE}/subscriptions", headers=hdrs, data=data)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_cancel_subscription":
                sub_id = arguments["subscription_id"]
                cancel_at_end = arguments.get("cancel_at_period_end", False)
                if cancel_at_end:
                    r = await c.post(
                        f"{STRIPE_BASE}/subscriptions/{sub_id}",
                        headers=hdrs,
                        data={"cancel_at_period_end": "true"},
                    )
                else:
                    r = await c.delete(f"{STRIPE_BASE}/subscriptions/{sub_id}", headers=hdrs)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_create_refund":
                data = {}
                if pi := arguments.get("payment_intent"):
                    data["payment_intent"] = pi
                if ch := arguments.get("charge"):
                    data["charge"] = ch
                if amount := arguments.get("amount"):
                    data["amount"] = str(amount)
                if reason := arguments.get("reason"):
                    data["reason"] = reason
                r = await c.post(f"{STRIPE_BASE}/refunds", headers=hdrs, data=data)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_list_invoices":
                params = {"limit": arguments.get("limit", 10)}
                for field in ("customer", "status", "starting_after"):
                    if v := arguments.get(field):
                        params[field] = v
                r = await c.get(f"{STRIPE_BASE}/invoices", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_create_invoice":
                data = {"customer": arguments["customer"]}
                for field in ("collection_method", "days_until_due", "description"):
                    if v := arguments.get(field):
                        data[field] = str(v)
                if meta := arguments.get("metadata"):
                    data.update(_flatten(meta, "metadata"))
                r = await c.post(f"{STRIPE_BASE}/invoices", headers=hdrs, data=data)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_list_products":
                params = {"limit": arguments.get("limit", 10)}
                if "active" in arguments:
                    params["active"] = str(arguments["active"]).lower()
                if sa := arguments.get("starting_after"):
                    params["starting_after"] = sa
                r = await c.get(f"{STRIPE_BASE}/products", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_create_product":
                data = {"name": arguments["name"]}
                for field in ("description",):
                    if v := arguments.get(field):
                        data[field] = v
                if meta := arguments.get("metadata"):
                    data.update(_flatten(meta, "metadata"))
                r = await c.post(f"{STRIPE_BASE}/products", headers=hdrs, data=data)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_list_prices":
                params = {"limit": arguments.get("limit", 10)}
                for field in ("product", "active", "type", "starting_after"):
                    if v := arguments.get(field):
                        params[field] = str(v).lower() if isinstance(v, bool) else v
                r = await c.get(f"{STRIPE_BASE}/prices", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_create_price":
                data = {
                    "product": arguments["product"],
                    "unit_amount": str(arguments["unit_amount"]),
                    "currency": arguments.get("currency", "usd"),
                }
                if rec := arguments.get("recurring"):
                    data.update(_flatten(rec, "recurring"))
                if nick := arguments.get("nickname"):
                    data["nickname"] = nick
                r = await c.post(f"{STRIPE_BASE}/prices", headers=hdrs, data=data)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_list_charges":
                params = {"limit": arguments.get("limit", 10)}
                for field in ("customer", "starting_after"):
                    if v := arguments.get(field):
                        params[field] = v
                if gte := arguments.get("created_gte"):
                    params["created[gte]"] = str(gte)
                if lte := arguments.get("created_lte"):
                    params["created[lte]"] = str(lte)
                r = await c.get(f"{STRIPE_BASE}/charges", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "stripe_create_payout":
                data = {
                    "amount": str(arguments["amount"]),
                    "currency": arguments.get("currency", "usd"),
                }
                if desc := arguments.get("description"):
                    data["description"] = desc
                r = await c.post(f"{STRIPE_BASE}/payouts", headers=hdrs, data=data)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("stripe_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
