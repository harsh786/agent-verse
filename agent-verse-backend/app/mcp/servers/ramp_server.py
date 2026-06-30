"""Ramp MCP server — corporate spend management, cards, and transaction approval.

Environment:
  RAMP_ACCESS_TOKEN: Ramp OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.ramp.com/developer/v1"

TOOL_DEFINITIONS = [
    {
        "name": "ramp_list_transactions",
        "description": "List corporate card transactions with optional date and status filters",
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "integer", "description": "Start Unix timestamp for transactions"},
                "end": {"type": "integer", "description": "End Unix timestamp for transactions"},
                "page_size": {"type": "integer", "description": "Transactions per page"},
                "start_cursor": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "ramp_list_cards",
        "description": "List corporate cards in the Ramp account",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Cards per page"},
                "start_cursor": {"type": "string", "description": "Pagination cursor"},
                "user_id": {"type": "string", "description": "Filter by cardholder user ID"},
            },
        },
    },
    {
        "name": "ramp_get_spend_limits",
        "description": "Get spend limits and policies for the Ramp account",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Results per page"},
                "start_cursor": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "ramp_list_departments",
        "description": "List departments configured in the Ramp account",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Departments per page"},
                "start_cursor": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "ramp_list_vendors",
        "description": "List merchants and vendors from Ramp transaction history",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Vendors per page"},
                "start_cursor": {"type": "string", "description": "Pagination cursor"},
            },
        },
    },
    {
        "name": "ramp_approve_transaction",
        "description": "Approve or decline a pending Ramp expense reimbursement",
        "parameters": {
            "type": "object",
            "properties": {
                "reimbursement_id": {"type": "string", "description": "ID of the reimbursement to approve"},
                "action": {"type": "string", "description": "Action to take: approve or decline"},
                "comment": {"type": "string", "description": "Optional approval comment"},
            },
            "required": ["reimbursement_id", "action"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("RAMP_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "RAMP_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "ramp_list_transactions":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/transactions", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "ramp_list_cards":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/cards", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "ramp_get_spend_limits":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/spend-limits", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "ramp_list_departments":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/departments", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "ramp_list_vendors":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/merchants", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "ramp_approve_transaction":
                r = await client.patch(
                    f"{BASE_URL}/reimbursements/{arguments['reimbursement_id']}",
                    headers=headers,
                    json={
                        "action": arguments["action"],
                        "comment": arguments.get("comment", ""),
                    },
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
