"""Zoho Invoice MCP server — invoicing, customers, and invoice lifecycle.

Environment:
  ZOHO_ACCESS_TOKEN:     Zoho OAuth2 access token
  ZOHO_ORGANIZATION_ID:  Zoho Invoice organization ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://www.zohoapis.com/invoice/v3"

TOOL_DEFINITIONS = [
    {
        "name": "zoho_invoice_list_invoices",
        "description": "List invoices in Zoho Invoice",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["draft", "sent", "overdue", "paid", "void", "partially_paid"],
                },
                "customer_id": {"type": "string"},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "zoho_invoice_create_invoice",
        "description": "Create a new invoice in Zoho Invoice",
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
        "name": "zoho_invoice_send_invoice",
        "description": "Email an invoice to the customer",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "to_mail_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of recipient email addresses",
                },
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "zoho_invoice_list_customers",
        "description": "List customers in Zoho Invoice",
        "parameters": {
            "type": "object",
            "properties": {
                "search_text": {"type": "string"},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "zoho_invoice_create_customer",
        "description": "Create a new customer in Zoho Invoice",
        "parameters": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "company_name": {"type": "string"},
            },
            "required": ["display_name"],
        },
    },
    {
        "name": "zoho_invoice_get_invoice_status",
        "description": "Get the status and details of a specific invoice",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
            },
            "required": ["invoice_id"],
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
            if tool_name == "zoho_invoice_list_invoices":
                params = {**params_base, "page": arguments.get("page", 1)}
                if status := arguments.get("status"):
                    params["status"] = status
                if cid := arguments.get("customer_id"):
                    params["customer_id"] = cid
                r = await c.get(f"{BASE}/invoices", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_invoice_create_invoice":
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

            elif tool_name == "zoho_invoice_send_invoice":
                inv_id = arguments["invoice_id"]
                payload = {}
                if to_ids := arguments.get("to_mail_ids"):
                    payload["to_mail_ids"] = to_ids
                if subj := arguments.get("subject"):
                    payload["subject"] = subj
                if body := arguments.get("body"):
                    payload["body"] = body
                r = await c.post(
                    f"{BASE}/invoices/{inv_id}/email",
                    json=payload,
                    params=params_base,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_invoice_list_customers":
                params = {**params_base, "page": arguments.get("page", 1)}
                if st := arguments.get("search_text"):
                    params["search_text"] = st
                r = await c.get(f"{BASE}/customers", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_invoice_create_customer":
                payload = {"display_name": arguments["display_name"]}
                for field in ("email", "phone", "company_name"):
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{BASE}/customers", json=payload, params=params_base)
                r.raise_for_status()
                return r.json()

            elif tool_name == "zoho_invoice_get_invoice_status":
                inv_id = arguments["invoice_id"]
                r = await c.get(f"{BASE}/invoices/{inv_id}", params=params_base)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("zoho_invoice_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
