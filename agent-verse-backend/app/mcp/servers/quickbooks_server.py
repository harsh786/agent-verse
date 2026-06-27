"""QuickBooks Online MCP server — accounting queries, invoices, customers, and payments.

Environment variables:
  QUICKBOOKS_ACCESS_TOKEN: OAuth2 bearer token
  QUICKBOOKS_COMPANY_ID:   Intuit company/realm ID
  QUICKBOOKS_SANDBOX:      'true' (default) or 'false' for production
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "qb_query",
        "description": "Execute a SQL-like QuickBooks query (e.g. SELECT * FROM Customer)",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "QuickBooks SQL-like query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "qb_create_invoice",
        "description": "Create an invoice in QuickBooks",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_ref_id": {"type": "string", "description": "QuickBooks customer ID"},
                "customer_ref_name": {"type": "string"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "amount": {"type": "number"},
                            "item_id": {"type": "string"},
                            "qty": {"type": "number", "default": 1},
                            "unit_price": {"type": "number"},
                        },
                        "required": ["amount"],
                    },
                },
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "private_note": {"type": "string"},
            },
            "required": ["customer_ref_id", "line_items"],
        },
    },
    {
        "name": "qb_create_customer",
        "description": "Create a new customer in QuickBooks",
        "parameters": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string"},
                "given_name": {"type": "string"},
                "family_name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "company_name": {"type": "string"},
            },
            "required": ["display_name"],
        },
    },
    {
        "name": "qb_list_accounts",
        "description": "List chart of accounts from QuickBooks",
        "parameters": {
            "type": "object",
            "properties": {
                "account_type": {
                    "type": "string",
                    "description": "Filter by account type e.g. 'Expense', 'Income', 'Asset'",
                },
                "max_results": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "qb_create_payment",
        "description": "Record a customer payment in QuickBooks",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_ref_id": {"type": "string"},
                "total_amount": {"type": "number"},
                "payment_method_ref_id": {"type": "string", "description": "QBO payment method ID"},
                "deposit_to_account_ref_id": {"type": "string"},
                "invoice_ref_id": {"type": "string", "description": "Invoice to apply payment to"},
            },
            "required": ["customer_ref_id", "total_amount"],
        },
    },
    {
        "name": "qb_list_vendors",
        "description": "List vendors from QuickBooks",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "qb_create_bill",
        "description": "Create a vendor bill in QuickBooks",
        "parameters": {
            "type": "object",
            "properties": {
                "vendor_ref_id": {"type": "string"},
                "line_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "number"},
                            "account_ref_id": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["amount", "account_ref_id"],
                    },
                },
                "due_date": {"type": "string"},
            },
            "required": ["vendor_ref_id", "line_items"],
        },
    },
    {
        "name": "qb_get_profit_loss",
        "description": "Generate a Profit and Loss report for a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "accounting_method": {"type": "string", "enum": ["Accrual", "Cash"], "default": "Accrual"},
            },
            "required": ["start_date", "end_date"],
        },
    },
]


def _base_url() -> str:
    sandbox = os.getenv("QUICKBOOKS_SANDBOX", "true").lower() == "true"
    base = "sandbox-quickbooks" if sandbox else "quickbooks"
    company_id = os.getenv("QUICKBOOKS_COMPANY_ID", "")
    return f"https://{base}.api.intuit.com/v3/company/{company_id}"


def _headers() -> dict[str, str]:
    token = os.getenv("QUICKBOOKS_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("QUICKBOOKS_ACCESS_TOKEN", "")
    company_id = os.getenv("QUICKBOOKS_COMPANY_ID", "")
    if not token or not company_id:
        return {"error": "QUICKBOOKS_ACCESS_TOKEN and QUICKBOOKS_COMPANY_ID required"}

    base = _base_url()
    hdrs = _headers()

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "qb_query":
                r = await c.get(
                    f"{base}/query",
                    headers=hdrs,
                    params={"query": arguments["query"], "minorversion": "65"},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "qb_create_invoice":
                lines = []
                for item in arguments["line_items"]:
                    line: dict[str, Any] = {
                        "Amount": item["amount"],
                        "DetailType": "SalesItemLineDetail" if item.get("item_id") else "DescriptionOnly",
                    }
                    if item.get("item_id"):
                        line["SalesItemLineDetail"] = {
                            "ItemRef": {"value": item["item_id"]},
                            "Qty": item.get("qty", 1),
                            "UnitPrice": item.get("unit_price", item["amount"]),
                        }
                    else:
                        line["Description"] = item.get("description", "")
                    lines.append(line)

                body: dict[str, Any] = {
                    "CustomerRef": {
                        "value": arguments["customer_ref_id"],
                        "name": arguments.get("customer_ref_name", ""),
                    },
                    "Line": lines,
                }
                if due := arguments.get("due_date"):
                    body["DueDate"] = due
                if note := arguments.get("private_note"):
                    body["PrivateNote"] = note

                r = await c.post(
                    f"{base}/invoice",
                    headers=hdrs,
                    params={"minorversion": "65"},
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "qb_create_customer":
                body = {"DisplayName": arguments["display_name"]}
                for qb_key, arg_key in [
                    ("GivenName", "given_name"),
                    ("FamilyName", "family_name"),
                    ("CompanyName", "company_name"),
                ]:
                    if v := arguments.get(arg_key):
                        body[qb_key] = v
                if email := arguments.get("email"):
                    body["PrimaryEmailAddr"] = {"Address": email}
                if phone := arguments.get("phone"):
                    body["PrimaryPhone"] = {"FreeFormNumber": phone}
                r = await c.post(
                    f"{base}/customer",
                    headers=hdrs,
                    params={"minorversion": "65"},
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "qb_list_accounts":
                where = ""
                if acct_type := arguments.get("account_type"):
                    where = f" WHERE AccountType = '{acct_type}'"
                max_r = arguments.get("max_results", 50)
                query = f"SELECT * FROM Account{where} MAXRESULTS {max_r}"
                r = await c.get(
                    f"{base}/query",
                    headers=hdrs,
                    params={"query": query, "minorversion": "65"},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "qb_create_payment":
                body = {
                    "CustomerRef": {"value": arguments["customer_ref_id"]},
                    "TotalAmt": arguments["total_amount"],
                }
                if pm := arguments.get("payment_method_ref_id"):
                    body["PaymentMethodRef"] = {"value": pm}
                if dep := arguments.get("deposit_to_account_ref_id"):
                    body["DepositToAccountRef"] = {"value": dep}
                if inv := arguments.get("invoice_ref_id"):
                    body["Line"] = [
                        {
                            "Amount": arguments["total_amount"],
                            "LinkedTxn": [{"TxnId": inv, "TxnType": "Invoice"}],
                        }
                    ]
                r = await c.post(
                    f"{base}/payment",
                    headers=hdrs,
                    params={"minorversion": "65"},
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "qb_list_vendors":
                max_r = arguments.get("max_results", 50)
                r = await c.get(
                    f"{base}/query",
                    headers=hdrs,
                    params={"query": f"SELECT * FROM Vendor MAXRESULTS {max_r}", "minorversion": "65"},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "qb_create_bill":
                lines = [
                    {
                        "Amount": item["amount"],
                        "DetailType": "AccountBasedExpenseLineDetail",
                        "AccountBasedExpenseLineDetail": {
                            "AccountRef": {"value": item["account_ref_id"]},
                        },
                        "Description": item.get("description", ""),
                    }
                    for item in arguments["line_items"]
                ]
                body = {
                    "VendorRef": {"value": arguments["vendor_ref_id"]},
                    "Line": lines,
                }
                if due := arguments.get("due_date"):
                    body["DueDate"] = due
                r = await c.post(
                    f"{base}/bill",
                    headers=hdrs,
                    params={"minorversion": "65"},
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "qb_get_profit_loss":
                params = {
                    "start_date": arguments["start_date"],
                    "end_date": arguments["end_date"],
                    "accounting_method": arguments.get("accounting_method", "Accrual"),
                    "minorversion": "65",
                }
                r = await c.get(f"{base}/reports/ProfitAndLoss", headers=hdrs, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("qb_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
