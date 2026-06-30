"""Zuora MCP server — subscription billing, accounts, invoices, and subscriptions.

Environment:
  ZUORA_CLIENT_ID:     Zuora OAuth client ID
  ZUORA_CLIENT_SECRET: Zuora OAuth client secret
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://rest.zuora.com/v1"
TOKEN_URL = "https://rest.zuora.com/oauth/token"

TOOL_DEFINITIONS = [
    {
        "name": "zuora_list_subscriptions",
        "description": "List subscriptions in Zuora",
        "parameters": {
            "type": "object",
            "properties": {
                "account_key": {"type": "string", "description": "Account number or ID"},
                "page_size": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "zuora_get_subscription",
        "description": "Get a specific subscription by subscription number or key",
        "parameters": {
            "type": "object",
            "properties": {
                "subscription_key": {"type": "string"},
            },
            "required": ["subscription_key"],
        },
    },
    {
        "name": "zuora_create_subscription",
        "description": "Create a new subscription for an account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_key": {"type": "string"},
                "contract_effective_date": {"type": "string", "description": "YYYY-MM-DD"},
                "subscribe_to_rate_plans": {"type": "array", "items": {"type": "object"}},
                "term_type": {"type": "string", "enum": ["TERMED", "EVERGREEN"], "default": "EVERGREEN"},
            },
            "required": ["account_key", "contract_effective_date", "subscribe_to_rate_plans"],
        },
    },
    {
        "name": "zuora_list_accounts",
        "description": "Query accounts in Zuora",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 20},
                "zoql": {"type": "string", "description": "Optional ZOQL filter, e.g. Status='Active'"},
            },
        },
    },
    {
        "name": "zuora_get_account",
        "description": "Get a specific Zuora account by key",
        "parameters": {
            "type": "object",
            "properties": {
                "account_key": {"type": "string"},
            },
            "required": ["account_key"],
        },
    },
    {
        "name": "zuora_create_invoice",
        "description": "Create and post an invoice for a Zuora account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_key": {"type": "string"},
                "invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
                "target_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["account_key", "invoice_date", "target_date"],
        },
    },
]


async def _get_token() -> str:
    client_id = os.getenv("ZUORA_CLIENT_ID", "")
    client_secret = os.getenv("ZUORA_CLIENT_SECRET", "")
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        )
        r.raise_for_status()
        return r.json()["access_token"]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    client_id = os.getenv("ZUORA_CLIENT_ID", "")
    client_secret = os.getenv("ZUORA_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return {"error": "ZUORA_CLIENT_ID and ZUORA_CLIENT_SECRET must be configured"}

    try:
        token = await _get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "zuora_list_subscriptions":
                params: dict[str, Any] = {"pageSize": arguments.get("page_size", 20)}
                if ak := arguments.get("account_key"):
                    r = await c.get(f"{BASE}/subscriptions/accounts/{ak}", params=params)
                else:
                    r = await c.get(f"{BASE}/subscriptions", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zuora_get_subscription":
                sk = arguments["subscription_key"]
                r = await c.get(f"{BASE}/subscriptions/{sk}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "zuora_create_subscription":
                payload: dict[str, Any] = {
                    "accountKey": arguments["account_key"],
                    "contractEffectiveDate": arguments["contract_effective_date"],
                    "subscribeToRatePlans": arguments["subscribe_to_rate_plans"],
                    "termType": arguments.get("term_type", "EVERGREEN"),
                }
                r = await c.post(f"{BASE}/subscriptions", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zuora_list_accounts":
                zoql = arguments.get("zoql", "select Id, Name, Status from Account")
                payload = {
                    "queryString": zoql if "select" in zoql.lower() else f"select Id, Name, Status from Account where {zoql}",
                }
                r = await c.post(f"{BASE}/action/query", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zuora_get_account":
                ak = arguments["account_key"]
                r = await c.get(f"{BASE}/accounts/{ak}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "zuora_create_invoice":
                payload = {
                    "accountKey": arguments["account_key"],
                    "invoiceDate": arguments["invoice_date"],
                    "targetDate": arguments["target_date"],
                }
                r = await c.post(f"{BASE}/operations/billing-preview", json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("zuora_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
