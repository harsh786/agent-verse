"""Xero MCP server — accounting and invoicing via Xero API.

Environment variables:
  XERO_ACCESS_TOKEN: OAuth2 bearer token
  XERO_TENANT_ID:    Xero tenant/organisation ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

XERO_BASE = "https://api.xero.com/api.xro/2.0"

TOOL_DEFINITIONS = [
    {
        "name": "xero_list_invoices",
        "description": "List Xero invoices with optional status and contact filter",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["DRAFT", "SUBMITTED", "AUTHORISED", "VOIDED", "DELETED", "PAID"],
                },
                "contact_id": {"type": "string"},
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "xero_create_invoice",
        "description": "Create an invoice in Xero",
        "parameters": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "type": {"type": "string", "enum": ["ACCREC", "ACCPAY"], "default": "ACCREC"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "quantity": {"type": "number", "default": 1},
                            "unit_amount": {"type": "number"},
                            "account_code": {"type": "string"},
                            "tax_type": {"type": "string"},
                        },
                        "required": ["description", "unit_amount"],
                    },
                },
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "status": {"type": "string", "enum": ["DRAFT", "AUTHORISED"], "default": "DRAFT"},
                "reference": {"type": "string"},
            },
            "required": ["contact_id", "line_items"],
        },
    },
    {
        "name": "xero_list_contacts",
        "description": "List Xero contacts (customers and suppliers)",
        "parameters": {
            "type": "object",
            "properties": {
                "search_term": {"type": "string"},
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 100},
                "is_customer": {"type": "boolean"},
                "is_supplier": {"type": "boolean"},
            },
        },
    },
    {
        "name": "xero_create_contact",
        "description": "Create a new Xero contact",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email_address": {"type": "string"},
                "phone": {"type": "string"},
                "is_customer": {"type": "boolean", "default": True},
                "is_supplier": {"type": "boolean", "default": False},
                "company_number": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "xero_list_accounts",
        "description": "List chart of accounts from Xero",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Account type e.g. BANK, CURRENT, EQUITY, EXPENSE, FIXED, LIABILITY, PREPAYMENT, REVENUE, SALES, TERMLIAB, OTHERINCOME",
                },
                "status": {"type": "string", "enum": ["ACTIVE", "ARCHIVED"], "default": "ACTIVE"},
            },
        },
    },
    {
        "name": "xero_create_payment",
        "description": "Record a payment against an invoice in Xero",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "account_id": {"type": "string", "description": "Bank account ID to receive payment"},
                "amount": {"type": "number"},
                "date": {"type": "string", "description": "YYYY-MM-DD payment date"},
                "reference": {"type": "string"},
            },
            "required": ["invoice_id", "account_id", "amount"],
        },
    },
    {
        "name": "xero_get_balance_sheet",
        "description": "Get a Balance Sheet report from Xero",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD report date (defaults to today)"},
                "periods": {"type": "integer", "default": 1},
                "timeframe": {"type": "string", "enum": ["MONTH", "QUARTER", "YEAR"], "default": "MONTH"},
            },
        },
    },
    {
        "name": "xero_list_bank_transactions",
        "description": "List bank transactions in Xero",
        "parameters": {
            "type": "object",
            "properties": {
                "bank_account_id": {"type": "string"},
                "from_date": {"type": "string", "description": "YYYY-MM-DD"},
                "to_date": {"type": "string"},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("XERO_ACCESS_TOKEN", "")
    tenant_id = os.getenv("XERO_TENANT_ID", "")
    return {
        "Authorization": f"Bearer {token}",
        "Xero-tenant-id": tenant_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("XERO_ACCESS_TOKEN", "")
    tenant_id = os.getenv("XERO_TENANT_ID", "")
    if not token or not tenant_id:
        return {"error": "XERO_ACCESS_TOKEN and XERO_TENANT_ID required"}

    hdrs = _headers()

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "xero_list_invoices":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "pageSize": arguments.get("page_size", 100),
                    "summaryOnly": True,
                }
                if status := arguments.get("status"):
                    params["Statuses"] = status
                if contact_id := arguments.get("contact_id"):
                    params["ContactIDs"] = contact_id
                r = await c.get(f"{XERO_BASE}/Invoices", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_create_invoice":
                lines = [
                    {
                        "Description": item["description"],
                        "Quantity": item.get("quantity", 1),
                        "UnitAmount": item["unit_amount"],
                        **({"AccountCode": item["account_code"]} if item.get("account_code") else {}),
                        **({"TaxType": item["tax_type"]} if item.get("tax_type") else {}),
                    }
                    for item in arguments["line_items"]
                ]
                body: dict[str, Any] = {
                    "Invoices": [
                        {
                            "Type": arguments.get("type", "ACCREC"),
                            "Contact": {"ContactID": arguments["contact_id"]},
                            "LineItems": lines,
                            "Status": arguments.get("status", "DRAFT"),
                        }
                    ]
                }
                if due := arguments.get("due_date"):
                    body["Invoices"][0]["DueDate"] = due
                if ref := arguments.get("reference"):
                    body["Invoices"][0]["Reference"] = ref
                r = await c.post(f"{XERO_BASE}/Invoices", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_list_contacts":
                params = {
                    "page": arguments.get("page", 1),
                    "pageSize": arguments.get("page_size", 100),
                }
                if term := arguments.get("search_term"):
                    params["searchTerm"] = term
                if arguments.get("is_customer"):
                    params["IsCustomer"] = "true"
                if arguments.get("is_supplier"):
                    params["IsSupplier"] = "true"
                r = await c.get(f"{XERO_BASE}/Contacts", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_create_contact":
                contact: dict[str, Any] = {
                    "Name": arguments["name"],
                    "IsCustomer": arguments.get("is_customer", True),
                    "IsSupplier": arguments.get("is_supplier", False),
                }
                if email := arguments.get("email_address"):
                    contact["EmailAddress"] = email
                if phone := arguments.get("phone"):
                    contact["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]
                if cn := arguments.get("company_number"):
                    contact["CompanyNumber"] = cn
                r = await c.post(
                    f"{XERO_BASE}/Contacts",
                    headers=hdrs,
                    json={"Contacts": [contact]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_list_accounts":
                params = {"Status": arguments.get("status", "ACTIVE")}
                if acct_type := arguments.get("type"):
                    params["Type"] = acct_type
                r = await c.get(f"{XERO_BASE}/Accounts", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_create_payment":
                body = {
                    "Payments": [
                        {
                            "Invoice": {"InvoiceID": arguments["invoice_id"]},
                            "Account": {"AccountID": arguments["account_id"]},
                            "Amount": arguments["amount"],
                        }
                    ]
                }
                if date := arguments.get("date"):
                    body["Payments"][0]["Date"] = date
                if ref := arguments.get("reference"):
                    body["Payments"][0]["Reference"] = ref
                r = await c.post(f"{XERO_BASE}/Payments", headers=hdrs, json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_get_balance_sheet":
                params = {
                    "periods": arguments.get("periods", 1),
                    "timeframe": arguments.get("timeframe", "MONTH"),
                }
                if date := arguments.get("date"):
                    params["date"] = date
                r = await c.get(f"{XERO_BASE}/Reports/BalanceSheet", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_list_bank_transactions":
                params = {"page": arguments.get("page", 1)}
                if bid := arguments.get("bank_account_id"):
                    params["BankAccountID"] = bid
                if fd := arguments.get("from_date"):
                    params["FromDate"] = fd
                if td := arguments.get("to_date"):
                    params["ToDate"] = td
                r = await c.get(f"{XERO_BASE}/BankTransactions", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("xero_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
