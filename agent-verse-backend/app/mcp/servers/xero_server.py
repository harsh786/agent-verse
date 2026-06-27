"""Xero MCP server — accounting, invoices, contacts, and transactions.

Environment:
  XERO_ACCESS_TOKEN: OAuth 2.0 Bearer token
  XERO_TENANT_ID:    Xero Tenant/Organisation ID
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
        "description": "List Xero invoices with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["DRAFT", "SUBMITTED", "DELETED", "AUTHORISED", "PAID", "VOIDED"],
                },
                "type": {
                    "type": "string",
                    "enum": ["ACCREC", "ACCPAY"],
                    "description": "ACCREC=accounts receivable, ACCPAY=accounts payable",
                },
                "contact_id": {"type": "string"},
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "xero_get_invoice",
        "description": "Get a specific Xero invoice by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "xero_create_invoice",
        "description": "Create a new Xero invoice",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["ACCREC", "ACCPAY"],
                    "default": "ACCREC",
                },
                "contact_id": {"type": "string"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "Description": {"type": "string"},
                            "Quantity": {"type": "number"},
                            "UnitAmount": {"type": "number"},
                            "AccountCode": {"type": "string"},
                        },
                    },
                },
                "status": {"type": "string", "enum": ["DRAFT", "SUBMITTED", "AUTHORISED"], "default": "DRAFT"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "reference": {"type": "string"},
                "currency_code": {"type": "string", "default": "USD"},
            },
            "required": ["contact_id", "line_items"],
        },
    },
    {
        "name": "xero_list_contacts",
        "description": "List Xero contacts (customers/suppliers)",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
                "contact_status": {"type": "string", "enum": ["ACTIVE", "ARCHIVED"]},
                "is_customer": {"type": "boolean"},
                "is_supplier": {"type": "boolean"},
                "page": {"type": "integer", "default": 1},
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
                "phone_number": {"type": "string"},
                "is_customer": {"type": "boolean", "default": True},
                "is_supplier": {"type": "boolean", "default": False},
                "tax_number": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "xero_list_accounts",
        "description": "List Xero chart of accounts",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Account type filter: BANK, CURRENT, EQUITY, EXPENSE, FIXED, LIABILITY, PREPAYMENT, REVENUE, SALES, TERMLIAB, OTHERINCOME",
                },
                "status": {"type": "string", "enum": ["ACTIVE", "ARCHIVED"]},
            },
        },
    },
    {
        "name": "xero_get_profit_loss",
        "description": "Get Profit & Loss report for a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "YYYY-MM-DD"},
                "periods": {"type": "integer", "description": "Number of periods"},
                "timeframe": {
                    "type": "string",
                    "enum": ["MONTH", "QUARTER", "YEAR"],
                },
            },
            "required": ["from_date", "to_date"],
        },
    },
    {
        "name": "xero_create_payment",
        "description": "Record a payment against a Xero invoice",
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
        return {"error": "XERO_ACCESS_TOKEN and XERO_TENANT_ID must be configured"}

    try:
        async with httpx.AsyncClient(base_url=XERO_BASE, headers=_headers(), timeout=30.0) as c:
            if tool_name == "xero_list_invoices":
                params: dict[str, Any] = {"page": arguments.get("page", 1)}
                conditions = []
                if status := arguments.get("status"):
                    conditions.append(f"Status==\"{status}\"")
                if inv_type := arguments.get("type"):
                    conditions.append(f"Type==\"{inv_type}\"")
                if cid := arguments.get("contact_id"):
                    conditions.append(f"Contact.ContactID=Guid(\"{cid}\")")
                if conditions:
                    params["where"] = " AND ".join(conditions)
                if df := arguments.get("date_from"):
                    params["DateFrom"] = df
                if dt := arguments.get("date_to"):
                    params["DateTo"] = dt
                r = await c.get("/Invoices", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_get_invoice":
                r = await c.get(f"/Invoices/{arguments['invoice_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_create_invoice":
                invoice: dict[str, Any] = {
                    "Type": arguments.get("type", "ACCREC"),
                    "Contact": {"ContactID": arguments["contact_id"]},
                    "LineItems": arguments["line_items"],
                    "Status": arguments.get("status", "DRAFT"),
                    "CurrencyCode": arguments.get("currency_code", "USD"),
                }
                if due := arguments.get("due_date"):
                    invoice["DueDate"] = f"/Date({due})/"
                if ref := arguments.get("reference"):
                    invoice["Reference"] = ref
                r = await c.put("/Invoices", json={"Invoices": [invoice]})
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_list_contacts":
                params = {"page": arguments.get("page", 1)}
                if search := arguments.get("search"):
                    params["searchFields"] = "EmailAddress,Name"
                    params["contactName"] = search
                if status := arguments.get("contact_status"):
                    params["where"] = f"ContactStatus==\"{status}\""
                r = await c.get("/Contacts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_create_contact":
                contact: dict[str, Any] = {"Name": arguments["name"]}
                if email := arguments.get("email_address"):
                    contact["EmailAddress"] = email
                if phone := arguments.get("phone_number"):
                    contact["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]
                if arguments.get("is_customer"):
                    contact["IsCustomer"] = True
                if arguments.get("is_supplier"):
                    contact["IsSupplier"] = True
                if tax := arguments.get("tax_number"):
                    contact["TaxNumber"] = tax
                r = await c.put("/Contacts", json={"Contacts": [contact]})
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_list_accounts":
                params = {}
                conditions = []
                if acc_type := arguments.get("type"):
                    conditions.append(f"Type==\"{acc_type}\"")
                if status := arguments.get("status"):
                    conditions.append(f"Status==\"{status}\"")
                if conditions:
                    params["where"] = " AND ".join(conditions)
                r = await c.get("/Accounts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_get_profit_loss":
                params = {
                    "fromDate": arguments["from_date"],
                    "toDate": arguments["to_date"],
                }
                if periods := arguments.get("periods"):
                    params["periods"] = periods
                if timeframe := arguments.get("timeframe"):
                    params["timeframe"] = timeframe
                r = await c.get("/Reports/ProfitAndLoss", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_create_payment":
                payment: dict[str, Any] = {
                    "Invoice": {"InvoiceID": arguments["invoice_id"]},
                    "Account": {"AccountID": arguments["account_id"]},
                    "Amount": arguments["amount"],
                }
                if date := arguments.get("date"):
                    payment["Date"] = date
                if ref := arguments.get("reference"):
                    payment["Reference"] = ref
                r = await c.put("/Payments", json={"Payments": [payment]})
                r.raise_for_status()
                return r.json()

            elif tool_name == "xero_list_bank_transactions":
                params = {"page": arguments.get("page", 1)}
                conditions = []
                if bid := arguments.get("bank_account_id"):
                    conditions.append(f"BankAccount.AccountID=Guid(\"{bid}\")")
                if fd := arguments.get("from_date"):
                    params["fromDate"] = fd
                if td := arguments.get("to_date"):
                    params["toDate"] = td
                if conditions:
                    params["where"] = " AND ".join(conditions)
                r = await c.get("/BankTransactions", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("xero_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
