"""FreshBooks MCP server — accounting, invoices, clients, and expenses.

Environment:
  FRESHBOOKS_ACCESS_TOKEN: FreshBooks OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.freshbooks.com"

TOOL_DEFINITIONS = [
    {
        "name": "freshbooks_list_invoices",
        "description": "List invoices for a FreshBooks account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "FreshBooks account ID"},
                "invoice_status": {
                    "type": "integer",
                    "description": "0=draft, 1=created, 2=sent, 4=viewed, 5=outstanding, 6=overdue, 7=disputed, 8=partial",
                },
                "per_page": {"type": "integer", "default": 15},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "freshbooks_create_invoice",
        "description": "Create a new invoice in FreshBooks",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "customerid": {"type": "integer", "description": "Client ID"},
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "unit_cost": {"type": "object"},
                            "qty": {"type": "integer"},
                        },
                    },
                },
                "due_offset_days": {"type": "integer", "default": 30},
                "currency_code": {"type": "string", "default": "USD"},
            },
            "required": ["account_id", "customerid"],
        },
    },
    {
        "name": "freshbooks_list_clients",
        "description": "List clients in a FreshBooks account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "per_page": {"type": "integer", "default": 15},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "freshbooks_create_client",
        "description": "Create a new client in FreshBooks",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "fname": {"type": "string", "description": "First name"},
                "lname": {"type": "string", "description": "Last name"},
                "email": {"type": "string"},
                "organization": {"type": "string"},
                "phone": {"type": "string"},
            },
            "required": ["account_id", "email"],
        },
    },
    {
        "name": "freshbooks_list_expenses",
        "description": "List expenses for a FreshBooks account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "per_page": {"type": "integer", "default": 15},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "freshbooks_create_expense",
        "description": "Create a new expense record in FreshBooks",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "amount": {"type": "object", "description": "Amount object e.g. {amount: '50.00', code: 'USD'}"},
                "categoryid": {"type": "integer"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "notes": {"type": "string"},
                "staffid": {"type": "integer"},
                "clientid": {"type": "integer"},
            },
            "required": ["account_id", "amount", "categoryid", "date"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("FRESHBOOKS_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "FRESHBOOKS_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "freshbooks_list_invoices":
                account_id = arguments["account_id"]
                params: dict[str, Any] = {
                    "per_page": arguments.get("per_page", 15),
                    "page": arguments.get("page", 1),
                }
                if status := arguments.get("invoice_status"):
                    params["invoice_status"] = status
                r = await c.get(f"{BASE}/accounting/account/{account_id}/invoices/invoices", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshbooks_create_invoice":
                account_id = arguments["account_id"]
                payload: dict[str, Any] = {
                    "invoice": {
                        "customerid": arguments["customerid"],
                        "due_offset_days": arguments.get("due_offset_days", 30),
                        "currency_code": arguments.get("currency_code", "USD"),
                    }
                }
                if lines := arguments.get("lines"):
                    payload["invoice"]["lines"] = lines
                r = await c.post(
                    f"{BASE}/accounting/account/{account_id}/invoices/invoices",
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshbooks_list_clients":
                account_id = arguments["account_id"]
                params = {
                    "per_page": arguments.get("per_page", 15),
                    "page": arguments.get("page", 1),
                }
                r = await c.get(f"{BASE}/accounting/account/{account_id}/users/clients", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshbooks_create_client":
                account_id = arguments["account_id"]
                client_data: dict[str, Any] = {"email": arguments["email"]}
                for field in ("fname", "lname", "organization", "phone"):
                    if v := arguments.get(field):
                        client_data[field] = v
                payload = {"client": client_data}
                r = await c.post(
                    f"{BASE}/accounting/account/{account_id}/users/clients",
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshbooks_list_expenses":
                account_id = arguments["account_id"]
                params = {
                    "per_page": arguments.get("per_page", 15),
                    "page": arguments.get("page", 1),
                }
                r = await c.get(f"{BASE}/accounting/account/{account_id}/expenses/expenses", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "freshbooks_create_expense":
                account_id = arguments["account_id"]
                expense_data: dict[str, Any] = {
                    "amount": arguments["amount"],
                    "categoryid": arguments["categoryid"],
                    "date": arguments["date"],
                }
                for field in ("notes", "staffid", "clientid"):
                    if v := arguments.get(field):
                        expense_data[field] = v
                payload = {"expense": expense_data}
                r = await c.post(
                    f"{BASE}/accounting/account/{account_id}/expenses/expenses",
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("freshbooks_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
