"""Chargebee MCP server — subscription billing, customers, and invoices.

Environment:
  CHARGEBEE_SITE:    Chargebee site name (e.g. 'mycompany')
  CHARGEBEE_API_KEY: Chargebee API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    site = os.getenv("CHARGEBEE_SITE", "")
    return f"https://{site}.chargebee.com/api/v2"


def _auth() -> tuple[str, str]:
    return (os.getenv("CHARGEBEE_API_KEY", ""), "")


TOOL_DEFINITIONS = [
    {
        "name": "chargebee_list_subscriptions",
        "description": "List Chargebee subscriptions with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "string", "description": "Pagination offset"},
                "status": {
                    "type": "string",
                    "enum": ["future", "in_trial", "active", "non_renewing", "paused", "cancelled"],
                },
                "plan_id": {"type": "string"},
                "customer_id": {"type": "string"},
            },
        },
    },
    {
        "name": "chargebee_get_subscription",
        "description": "Get a Chargebee subscription by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "subscription_id": {"type": "string"},
            },
            "required": ["subscription_id"],
        },
    },
    {
        "name": "chargebee_list_customers",
        "description": "List Chargebee customers",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "string"},
                "email": {"type": "string"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
            },
        },
    },
    {
        "name": "chargebee_get_customer",
        "description": "Get a Chargebee customer by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "chargebee_list_invoices",
        "description": "List Chargebee invoices",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["paid", "posted", "payment_due", "not_paid", "voided", "pending"],
                },
                "customer_id": {"type": "string"},
                "subscription_id": {"type": "string"},
                "date_from": {"type": "integer", "description": "Unix timestamp"},
                "date_to": {"type": "integer", "description": "Unix timestamp"},
            },
        },
    },
    {
        "name": "chargebee_create_customer",
        "description": "Create a new Chargebee customer",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "phone": {"type": "string"},
                "company": {"type": "string"},
                "locale": {"type": "string", "default": "en-US"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "chargebee_cancel_subscription",
        "description": "Cancel a Chargebee subscription",
        "parameters": {
            "type": "object",
            "properties": {
                "subscription_id": {"type": "string"},
                "end_of_term": {
                    "type": "boolean",
                    "default": True,
                    "description": "Cancel at end of current billing period",
                },
            },
            "required": ["subscription_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    site = os.getenv("CHARGEBEE_SITE", "")
    api_key = os.getenv("CHARGEBEE_API_KEY", "")
    if not site or not api_key:
        return {"error": "CHARGEBEE_SITE and CHARGEBEE_API_KEY must be configured"}

    base = _base()

    try:
        async with httpx.AsyncClient(auth=_auth(), timeout=30.0) as c:
            if tool_name == "chargebee_list_subscriptions":
                params: dict[str, Any] = {"limit": arguments.get("limit", 100)}
                if offset := arguments.get("offset"):
                    params["offset"] = offset
                if status := arguments.get("status"):
                    params["status[is]"] = status
                if plan := arguments.get("plan_id"):
                    params["plan_id[is]"] = plan
                if cid := arguments.get("customer_id"):
                    params["customer_id[is]"] = cid
                r = await c.get(f"{base}/subscriptions", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "chargebee_get_subscription":
                r = await c.get(f"{base}/subscriptions/{arguments['subscription_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "chargebee_list_customers":
                params = {"limit": arguments.get("limit", 100)}
                if offset := arguments.get("offset"):
                    params["offset"] = offset
                if email := arguments.get("email"):
                    params["email[is]"] = email
                if fn := arguments.get("first_name"):
                    params["first_name[is]"] = fn
                if ln := arguments.get("last_name"):
                    params["last_name[is]"] = ln
                r = await c.get(f"{base}/customers", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "chargebee_get_customer":
                r = await c.get(f"{base}/customers/{arguments['customer_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "chargebee_list_invoices":
                params = {"limit": arguments.get("limit", 100)}
                if offset := arguments.get("offset"):
                    params["offset"] = offset
                if status := arguments.get("status"):
                    params["status[is]"] = status
                if cid := arguments.get("customer_id"):
                    params["customer_id[is]"] = cid
                if sid := arguments.get("subscription_id"):
                    params["subscription_id[is]"] = sid
                if df := arguments.get("date_from"):
                    params["date[after]"] = df
                if dt := arguments.get("date_to"):
                    params["date[before]"] = dt
                r = await c.get(f"{base}/invoices", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "chargebee_create_customer":
                payload: dict[str, Any] = {"email": arguments["email"]}
                for field in ["first_name", "last_name", "phone", "company", "locale"]:
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{base}/customers", data=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "chargebee_cancel_subscription":
                sid = arguments["subscription_id"]
                payload = {"end_of_term": str(arguments.get("end_of_term", True)).lower()}
                r = await c.post(f"{base}/subscriptions/{sid}/cancel", data=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("chargebee_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
