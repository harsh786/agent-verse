"""Zoho Books MCP server — accounting, invoices, contacts, and expenses.

Environment:
  ZOHO_ACCESS_TOKEN:     Zoho OAuth2 access token
  ZOHO_ORGANIZATION_ID:  Zoho Books organization ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://www.zohoapis.com/books/v3"

TOOL_DEFINITIONS = [
    {
        "name": "zoho_books_list_invoices",
        "description": "List invoices in Zoho Books",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["draft", "sent", "overdue", "paid", "void", "unpaid", "partially_paid", "viewed"],
                },
                "customer_id": {"type": "string"},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "zoho_books_create_invoice",
        "description": "Create a new invoice in Zoho Books",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "invoice_number": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "due_date": {"type": "string"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_id": {"type": "string"},
                            "name": {"type": "string"},
                            "quantity": {"type": "number"},
                            "rate": {"type": "number"},
                        },
                    },
                },
            },
            "required": ["customer_id", "line_items"],
        },
    },
    {
        "name": "zoho_books_list_contacts",
        "description": "List contacts (customers and vendors) in Zoho Books",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_type": {"type": "string", "enum": ["customer", "vendor"]},
                "search_text": {"type": "string"},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "zoho_books_create_contact",
        "description": "Create a new contact in Zoho Books",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_name": {"type": "string"},
                "contact_type": {"type": "string", "enum": ["customer", "vendor"], "default": "customer"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "company_name": {"type": "string"},
            },
            "required": ["contact_name"],
        },
    },
    {
        "name": "zoho_books_list_expenses",
        "description": "List expenses in Zoho Books",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["unbilled", "invoiced", "reimbursed", "non_billable"]},
                "date_start": {"type": "string"},
                "date_end": {"type": "string"},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "zoho_books_get_dashboard",
        "description": "Get Zoho Books dashboard summary metrics",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("ZOHO_ACCESS_TOKEN", "")
    org_id = os.getenv("ZOHO_ORGANIZATION_ID", "")
    if not access_token or not org_id:
        return {"error": "ZOHO_ACCESS_TOKEN and ZOHO_ORGANIZATION_ID must be configured"}

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json",
    }
    params_base: dict[str, Any] = {"organization_id": org_id}

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "zoho_books_list_invoices":
                params = {**params_base, "page": arguments.get("page", 1)}
                if status := arguments.get("status"):
                    params["status"] = status
                if cid := arguments.get("customer_id"):
                    params["customer_id"] = cid
                r = await c.get(f"{BASE}/invoices", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_books_create_invoice":
                payload: dict[str, Any] = {
                    "customer_id": arguments["customer_id"],
                    "line_items": arguments["line_items"],
                }
                for field in ("invoice_number", "date", "due_date"):
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{BASE}/invoices", json=payload, params=params_base)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_books_list_contacts":
                params = {**params_base, "page": arguments.get("page", 1)}
                if ct := arguments.get("contact_type"):
                    params["contact_type"] = ct
                if st := arguments.get("search_text"):
                    params["search_text"] = st
                r = await c.get(f"{BASE}/contacts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_books_create_contact":
                payload = {
                    "contact_name": arguments["contact_name"],
                    "contact_type": arguments.get("contact_type", "customer"),
                }
                for field in ("email", "phone", "company_name"):
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{BASE}/contacts", json=payload, params=params_base)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_books_list_expenses":
                params = {**params_base, "page": arguments.get("page", 1)}
                if status := arguments.get("status"):
                    params["filter_by"] = f"Status.{status.replace('_', '')}"
                if ds := arguments.get("date_start"):
                    params["date_start"] = ds
                if de := arguments.get("date_end"):
                    params["date_end"] = de
                r = await c.get(f"{BASE}/expenses", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_books_get_dashboard":
                r = await c.get(f"{BASE}/dashboards", params=params_base)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("zoho_books_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
