"""Brex MCP server — corporate card management, transactions, and expense tracking.

Environment:
  BREX_TOKEN: Brex API token for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://platform.brexapis.com"

TOOL_DEFINITIONS = [
    {
        "name": "brex_list_accounts",
        "description": "List Brex cash accounts and credit accounts for the company",
        "parameters": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string", "description": "Pagination cursor"},
                "limit": {"type": "integer", "description": "Maximum accounts to return"},
            },
        },
    },
    {
        "name": "brex_get_transactions",
        "description": "Get transactions for Brex accounts with optional date and amount filters",
        "parameters": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string", "description": "Pagination cursor"},
                "limit": {"type": "integer", "description": "Maximum transactions to return"},
                "updated_after": {"type": "string", "description": "Filter transactions updated after this datetime"},
            },
        },
    },
    {
        "name": "brex_list_cards",
        "description": "List corporate cards issued in the Brex account",
        "parameters": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string", "description": "Pagination cursor"},
                "limit": {"type": "integer", "description": "Cards per page"},
                "user_id": {"type": "string", "description": "Filter cards by assigned user ID"},
            },
        },
    },
    {
        "name": "brex_get_spending_limits",
        "description": "Get spending limits for Brex cards or budgets",
        "parameters": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string", "description": "Pagination cursor"},
                "limit": {"type": "integer", "description": "Limits per page"},
            },
        },
    },
    {
        "name": "brex_list_vendors",
        "description": "List approved vendors and merchants in the Brex system",
        "parameters": {
            "type": "object",
            "properties": {
                "cursor": {"type": "string", "description": "Pagination cursor"},
                "limit": {"type": "integer", "description": "Vendors per page"},
            },
        },
    },
    {
        "name": "brex_create_expense",
        "description": "Create an expense memo for a Brex transaction",
        "parameters": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "Brex transaction ID to create expense for"},
                "memo": {"type": "string", "description": "Expense memo or description"},
                "category": {"type": "string", "description": "Expense category"},
                "budget_id": {"type": "string", "description": "Budget to allocate the expense to"},
            },
            "required": ["transaction_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    token = os.getenv("BREX_TOKEN", "")
    if not token:
        return {"error": "BREX_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "brex_list_accounts":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/v2/accounts", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "brex_get_transactions":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/v2/transactions/card/primary", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "brex_list_cards":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/v2/cards", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "brex_get_spending_limits":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/v1/budgets", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "brex_list_vendors":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/v1/vendors", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "brex_create_expense":
                payload: dict[str, Any] = {}
                if "memo" in arguments:
                    payload["memo"] = arguments["memo"]
                if "category" in arguments:
                    payload["expense_category_id"] = arguments["category"]
                if "budget_id" in arguments:
                    payload["budget_id"] = arguments["budget_id"]
                r = await client.patch(
                    f"{BASE_URL}/v1/expenses/card/{arguments['transaction_id']}",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
