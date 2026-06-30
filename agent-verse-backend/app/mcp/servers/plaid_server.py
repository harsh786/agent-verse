"""Plaid MCP server — financial data aggregation and bank account access.

Environment:
  PLAID_CLIENT_ID: Plaid client ID
  PLAID_SECRET: Plaid secret key for the selected environment
  PLAID_ACCESS_TOKEN: Plaid access token for a linked Item
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://production.plaid.com"

TOOL_DEFINITIONS = [
    {
        "name": "plaid_get_accounts",
        "description": "Get all bank accounts associated with a Plaid Item/access token",
        "parameters": {
            "type": "object",
            "properties": {
                "account_ids": {
                    "type": "array",
                    "description": "Optional list of specific account IDs to retrieve",
                    "items": {"type": "string"},
                },
            },
        },
    },
    {
        "name": "plaid_get_transactions",
        "description": "Get transactions for a linked bank account within a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                "account_ids": {
                    "type": "array",
                    "description": "Filter by specific account IDs",
                    "items": {"type": "string"},
                },
                "count": {"type": "integer", "description": "Number of transactions to return (max 500)"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "plaid_get_balance",
        "description": "Get real-time balance information for linked accounts",
        "parameters": {
            "type": "object",
            "properties": {
                "account_ids": {
                    "type": "array",
                    "description": "Specific account IDs to get balance for",
                    "items": {"type": "string"},
                },
            },
        },
    },
    {
        "name": "plaid_get_identity",
        "description": "Get identity information (name, address, etc.) of account holders",
        "parameters": {
            "type": "object",
            "properties": {
                "account_ids": {
                    "type": "array",
                    "description": "Account IDs to retrieve identity for",
                    "items": {"type": "string"},
                },
            },
        },
    },
    {
        "name": "plaid_get_investment_holdings",
        "description": "Get investment holdings for linked brokerage/investment accounts",
        "parameters": {
            "type": "object",
            "properties": {
                "account_ids": {
                    "type": "array",
                    "description": "Investment account IDs",
                    "items": {"type": "string"},
                },
            },
        },
    },
    {
        "name": "plaid_create_link_token",
        "description": "Create a Link token to initialize Plaid Link for a new user",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Your application's user ID"},
                "client_name": {"type": "string", "description": "Name of your application shown in Plaid Link"},
                "products": {
                    "type": "array",
                    "description": "Plaid products to request: transactions, auth, identity, etc.",
                    "items": {"type": "string"},
                },
                "country_codes": {
                    "type": "array",
                    "description": "Country codes (e.g. US, GB, CA)",
                    "items": {"type": "string"},
                },
                "language": {"type": "string", "description": "Language for Plaid Link UI (e.g. en)"},
            },
            "required": ["user_id", "client_name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    client_id = os.getenv("PLAID_CLIENT_ID", "")
    secret = os.getenv("PLAID_SECRET", "")
    access_token = os.getenv("PLAID_ACCESS_TOKEN", "")

    if not client_id or not secret:
        return {"error": "PLAID_CLIENT_ID and PLAID_SECRET not configured"}

    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            base_payload = {"client_id": client_id, "secret": secret}

            if tool_name == "plaid_get_accounts":
                if not access_token:
                    return {"error": "PLAID_ACCESS_TOKEN not configured"}
                payload = {**base_payload, "access_token": access_token}
                if "account_ids" in arguments:
                    payload["options"] = {"account_ids": arguments["account_ids"]}
                r = await client.post(f"{BASE_URL}/accounts/get", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "plaid_get_transactions":
                if not access_token:
                    return {"error": "PLAID_ACCESS_TOKEN not configured"}
                payload = {
                    **base_payload,
                    "access_token": access_token,
                    "start_date": arguments["start_date"],
                    "end_date": arguments["end_date"],
                }
                options: dict[str, Any] = {}
                if "account_ids" in arguments:
                    options["account_ids"] = arguments["account_ids"]
                if "count" in arguments:
                    options["count"] = arguments["count"]
                if "offset" in arguments:
                    options["offset"] = arguments["offset"]
                if options:
                    payload["options"] = options
                r = await client.post(f"{BASE_URL}/transactions/get", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "plaid_get_balance":
                if not access_token:
                    return {"error": "PLAID_ACCESS_TOKEN not configured"}
                payload = {**base_payload, "access_token": access_token}
                if "account_ids" in arguments:
                    payload["options"] = {"account_ids": arguments["account_ids"]}
                r = await client.post(f"{BASE_URL}/accounts/balance/get", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "plaid_get_identity":
                if not access_token:
                    return {"error": "PLAID_ACCESS_TOKEN not configured"}
                payload = {**base_payload, "access_token": access_token}
                if "account_ids" in arguments:
                    payload["options"] = {"account_ids": arguments["account_ids"]}
                r = await client.post(f"{BASE_URL}/identity/get", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "plaid_get_investment_holdings":
                if not access_token:
                    return {"error": "PLAID_ACCESS_TOKEN not configured"}
                payload = {**base_payload, "access_token": access_token}
                if "account_ids" in arguments:
                    payload["options"] = {"account_ids": arguments["account_ids"]}
                r = await client.post(f"{BASE_URL}/investments/holdings/get", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "plaid_create_link_token":
                payload = {
                    **base_payload,
                    "user": {"client_user_id": arguments["user_id"]},
                    "client_name": arguments["client_name"],
                    "products": arguments.get("products", ["transactions"]),
                    "country_codes": arguments.get("country_codes", ["US"]),
                    "language": arguments.get("language", "en"),
                }
                r = await client.post(f"{BASE_URL}/link/token/create", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
