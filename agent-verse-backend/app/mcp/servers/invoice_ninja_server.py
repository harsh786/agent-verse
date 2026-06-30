"""Invoice Ninja MCP server — invoicing, clients, and payments.

Environment:
  INVOICE_NINJA_TOKEN: Invoice Ninja API token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://app.invoiceninja.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "invoice_ninja_list_invoices",
        "description": "List invoices in Invoice Ninja",
        "parameters": {
            "type": "object",
            "properties": {
                "status_id": {
                    "type": "string",
                    "description": "1=draft, 2=sent, 3=partial, 4=paid, 5=cancelled, 6=reversed",
                },
                "client_id": {"type": "string"},
                "per_page": {"type": "integer", "default": 15},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "invoice_ninja_create_invoice",
        "description": "Create a new invoice",
        "parameters": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_key": {"type": "string"},
                            "notes": {"type": "string"},
                            "cost": {"type": "number"},
                            "quantity": {"type": "number"},
                        },
                    },
                },
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "po_number": {"type": "string"},
                "discount": {"type": "number"},
                "public_notes": {"type": "string"},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "invoice_ninja_list_clients",
        "description": "List clients in Invoice Ninja",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 15},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "invoice_ninja_create_client",
        "description": "Create a new client",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "website": {"type": "string"},
                "address1": {"type": "string"},
                "city": {"type": "string"},
                "country_id": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "invoice_ninja_list_payments",
        "description": "List payments recorded in Invoice Ninja",
        "parameters": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "per_page": {"type": "integer", "default": 15},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "invoice_ninja_send_invoice",
        "description": "Send an invoice by email to the client",
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
    api_token = os.getenv("INVOICE_NINJA_TOKEN", "")
    if not api_token:
        return {"error": "INVOICE_NINJA_TOKEN not configured"}

    headers = {
        "X-Api-Token": api_token,
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "invoice_ninja_list_invoices":
                params: dict[str, Any] = {
                    "per_page": arguments.get("per_page", 15),
                    "page": arguments.get("page", 1),
                }
                if sid := arguments.get("status_id"):
                    params["status_id"] = sid
                if cid := arguments.get("client_id"):
                    params["client_id"] = cid
                r = await c.get(f"{BASE}/invoices", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "invoice_ninja_create_invoice":
                payload: dict[str, Any] = {"client_id": arguments["client_id"]}
                for field in ("line_items", "due_date", "po_number", "discount", "public_notes"):
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{BASE}/invoices", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "invoice_ninja_list_clients":
                params = {
                    "per_page": arguments.get("per_page", 15),
                    "page": arguments.get("page", 1),
                }
                r = await c.get(f"{BASE}/clients", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "invoice_ninja_create_client":
                payload = {"name": arguments["name"]}
                for field in ("email", "phone", "website", "address1", "city", "country_id"):
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{BASE}/clients", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "invoice_ninja_list_payments":
                params = {
                    "per_page": arguments.get("per_page", 15),
                    "page": arguments.get("page", 1),
                }
                if cid := arguments.get("client_id"):
                    params["client_id"] = cid
                r = await c.get(f"{BASE}/payments", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "invoice_ninja_send_invoice":
                inv_id = arguments["invoice_id"]
                r = await c.post(f"{BASE}/emails", json={"entity": "invoice", "entity_id": inv_id})
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("invoice_ninja_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
