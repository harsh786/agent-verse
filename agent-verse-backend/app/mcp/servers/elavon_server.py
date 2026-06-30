"""Elavon MCP server — payment processing, transaction management, and batch operations.

Environment:
  ELAVON_MERCHANT_ID: Elavon merchant account ID
  ELAVON_USER_ID: Elavon API user ID
  ELAVON_PIN: Elavon transaction PIN
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.elavon.com/merchant"

TOOL_DEFINITIONS = [
    {
        "name": "elavon_process_payment",
        "description": "Process a credit card or debit card payment transaction",
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "string", "description": "Transaction amount in dollars (e.g. '10.50')"},
                "card_number": {"type": "string", "description": "Credit card number"},
                "exp_date": {"type": "string", "description": "Card expiration date (MMYY)"},
                "cvv": {"type": "string", "description": "Card verification value"},
                "card_holder": {"type": "string", "description": "Name on the card"},
                "invoice_number": {"type": "string", "description": "Optional invoice reference"},
            },
            "required": ["amount", "card_number", "exp_date"],
        },
    },
    {
        "name": "elavon_void_transaction",
        "description": "Void (cancel) a previously authorized transaction",
        "parameters": {
            "type": "object",
            "properties": {
                "txn_id": {"type": "string", "description": "Transaction ID to void"},
            },
            "required": ["txn_id"],
        },
    },
    {
        "name": "elavon_refund_transaction",
        "description": "Issue a refund for a settled transaction",
        "parameters": {
            "type": "object",
            "properties": {
                "txn_id": {"type": "string", "description": "Original transaction ID"},
                "amount": {"type": "string", "description": "Refund amount (full or partial)"},
            },
            "required": ["txn_id"],
        },
    },
    {
        "name": "elavon_list_transactions",
        "description": "List recent transactions with optional date and status filters",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date MM/DD/YYYY"},
                "end_date": {"type": "string", "description": "End date MM/DD/YYYY"},
                "result_code": {"type": "string", "description": "Filter by result: A (approved), D (declined)"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "elavon_get_account_summary",
        "description": "Get merchant account summary including daily totals",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date for summary MM/DD/YYYY"},
            },
        },
    },
    {
        "name": "elavon_batch_close",
        "description": "Close the current open batch to settle all transactions",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    merchant_id = os.getenv("ELAVON_MERCHANT_ID", "")
    user_id = os.getenv("ELAVON_USER_ID", "")
    pin = os.getenv("ELAVON_PIN", "")
    if not merchant_id or not user_id or not pin:
        return {"error": "ELAVON_MERCHANT_ID, ELAVON_USER_ID, and ELAVON_PIN not configured"}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            base_params: dict[str, Any] = {
                "ssl_merchant_id": merchant_id,
                "ssl_user_id": user_id,
                "ssl_pin": pin,
                "ssl_show_form": "false",
                "ssl_result_format": "ASCII",
            }

            if tool_name == "elavon_process_payment":
                params = {
                    **base_params,
                    "ssl_transaction_type": "ccsale",
                    "ssl_amount": arguments["amount"],
                    "ssl_card_number": arguments["card_number"],
                    "ssl_exp_date": arguments["exp_date"],
                }
                if "cvv" in arguments:
                    params["ssl_cvv2cvc2"] = arguments["cvv"]
                if "card_holder" in arguments:
                    params["ssl_first_name"] = arguments["card_holder"].split()[0]
                if "invoice_number" in arguments:
                    params["ssl_invoice_number"] = arguments["invoice_number"]
                r = await client.post(f"{BASE_URL}/processxml.do", data=params)
                r.raise_for_status()
                return {"response": r.text[:500]}

            if tool_name == "elavon_void_transaction":
                params = {**base_params, "ssl_transaction_type": "ccvoid", "ssl_txn_id": arguments["txn_id"]}
                r = await client.post(f"{BASE_URL}/processxml.do", data=params)
                r.raise_for_status()
                return {"response": r.text[:500]}

            if tool_name == "elavon_refund_transaction":
                params = {**base_params, "ssl_transaction_type": "ccreturn", "ssl_txn_id": arguments["txn_id"]}
                if "amount" in arguments:
                    params["ssl_amount"] = arguments["amount"]
                r = await client.post(f"{BASE_URL}/processxml.do", data=params)
                r.raise_for_status()
                return {"response": r.text[:500]}

            if tool_name == "elavon_list_transactions":
                params = {**base_params, "ssl_transaction_type": "txnquery"}
                if "start_date" in arguments:
                    params["ssl_start_date"] = arguments["start_date"]
                if "end_date" in arguments:
                    params["ssl_end_date"] = arguments["end_date"]
                if "result_code" in arguments:
                    params["ssl_result"] = arguments["result_code"]
                r = await client.post(f"{BASE_URL}/processxml.do", data=params)
                r.raise_for_status()
                return {"response": r.text[:2000]}

            if tool_name == "elavon_get_account_summary":
                params = {**base_params, "ssl_transaction_type": "txnquery"}
                if "date" in arguments:
                    params["ssl_start_date"] = arguments["date"]
                    params["ssl_end_date"] = arguments["date"]
                r = await client.post(f"{BASE_URL}/processxml.do", data=params)
                r.raise_for_status()
                return {"response": r.text[:2000]}

            if tool_name == "elavon_batch_close":
                params = {**base_params, "ssl_transaction_type": "batchclose"}
                r = await client.post(f"{BASE_URL}/processxml.do", data=params)
                r.raise_for_status()
                return {"response": r.text[:500]}

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
