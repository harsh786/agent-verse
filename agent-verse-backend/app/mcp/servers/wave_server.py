"""Wave MCP server — accounting, invoices, customers, and transactions via GraphQL.

Environment:
  WAVE_ACCESS_TOKEN: Wave Apps OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://gql.waveapps.com/graphql/public"

TOOL_DEFINITIONS = [
    {
        "name": "wave_list_businesses",
        "description": "List all businesses accessible to the Wave account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "wave_list_customers",
        "description": "List customers for a Wave business",
        "parameters": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string", "description": "Wave business ID"},
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 10},
            },
            "required": ["business_id"],
        },
    },
    {
        "name": "wave_list_invoices",
        "description": "List invoices for a Wave business",
        "parameters": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["DRAFT", "SAVED", "OUTSTANDING", "OVERDUE", "PAID", "PARTIAL"],
                },
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 10},
            },
            "required": ["business_id"],
        },
    },
    {
        "name": "wave_create_invoice",
        "description": "Create a new invoice in Wave",
        "parameters": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "customer_id": {"type": "string"},
                "invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "memo": {"type": "string"},
            },
            "required": ["business_id", "customer_id"],
        },
    },
    {
        "name": "wave_list_transactions",
        "description": "List accounting transactions for a Wave business",
        "parameters": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
                "page": {"type": "integer", "default": 1},
                "page_size": {"type": "integer", "default": 10},
            },
            "required": ["business_id"],
        },
    },
    {
        "name": "wave_get_account_balances",
        "description": "Get account balances (chart of accounts) for a Wave business",
        "parameters": {
            "type": "object",
            "properties": {
                "business_id": {"type": "string"},
            },
            "required": ["business_id"],
        },
    },
]


async def _gql(client: httpx.AsyncClient, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    r = await client.post(BASE, json=payload)
    r.raise_for_status()
    return r.json()


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("WAVE_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "WAVE_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "wave_list_businesses":
                query = """
                query { businesses { edges { node { id name isPersonal currency { code } } } } }
                """
                return await _gql(c, query)

            elif tool_name == "wave_list_customers":
                query = """
                query ($businessId: ID!, $page: Int!, $pageSize: Int!) {
                  business(id: $businessId) {
                    customers(page: $page, pageSize: $pageSize) {
                      pageInfo { totalPages currentPage }
                      edges { node { id name email } }
                    }
                  }
                }
                """
                variables = {
                    "businessId": arguments["business_id"],
                    "page": arguments.get("page", 1),
                    "pageSize": arguments.get("page_size", 10),
                }
                return await _gql(c, query, variables)

            elif tool_name == "wave_list_invoices":
                query = """
                query ($businessId: ID!, $page: Int!, $pageSize: Int!) {
                  business(id: $businessId) {
                    invoices(page: $page, pageSize: $pageSize) {
                      pageInfo { totalPages currentPage }
                      edges { node { id invoiceNumber status amountDue { raw } customer { name } } }
                    }
                  }
                }
                """
                variables = {
                    "businessId": arguments["business_id"],
                    "page": arguments.get("page", 1),
                    "pageSize": arguments.get("page_size", 10),
                }
                return await _gql(c, query, variables)

            elif tool_name == "wave_create_invoice":
                query = """
                mutation ($input: InvoiceCreateInput!) {
                  invoiceCreate(input: $input) {
                    didSucceed
                    inputErrors { code message path }
                    invoice { id invoiceNumber status }
                  }
                }
                """
                inp: dict[str, Any] = {
                    "businessId": arguments["business_id"],
                    "customerId": arguments["customer_id"],
                }
                if date := arguments.get("invoice_date"):
                    inp["invoiceDate"] = date
                if due := arguments.get("due_date"):
                    inp["dueDate"] = due
                if memo := arguments.get("memo"):
                    inp["memo"] = memo
                return await _gql(c, query, {"input": inp})

            elif tool_name == "wave_list_transactions":
                query = """
                query ($businessId: ID!, $page: Int!, $pageSize: Int!) {
                  business(id: $businessId) {
                    transactions(page: $page, pageSize: $pageSize) {
                      pageInfo { totalPages currentPage }
                      edges { node { id description amount { raw } date } }
                    }
                  }
                }
                """
                variables = {
                    "businessId": arguments["business_id"],
                    "page": arguments.get("page", 1),
                    "pageSize": arguments.get("page_size", 10),
                }
                return await _gql(c, query, variables)

            elif tool_name == "wave_get_account_balances":
                query = """
                query ($businessId: ID!) {
                  business(id: $businessId) {
                    accounts { edges { node { id name normalBalanceType subtype { name value } balance { raw } } } }
                  }
                }
                """
                return await _gql(c, query, {"businessId": arguments["business_id"]})

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("wave_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
