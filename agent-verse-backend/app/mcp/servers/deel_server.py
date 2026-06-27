"""Deel MCP server — global payroll, contracts, and compliance.

Environment:
  DEEL_API_KEY: Deel API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

DEEL_BASE = "https://api.letsdeel.com/rest/v2"

TOOL_DEFINITIONS = [
    {
        "name": "deel_list_contracts",
        "description": "List Deel contracts (employees, contractors, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
                "status": {
                    "type": "string",
                    "enum": ["active", "pending", "terminated", "all"],
                    "default": "active",
                },
                "type": {
                    "type": "string",
                    "description": "Contract type filter (e.g. 'employee', 'contractor')",
                },
            },
        },
    },
    {
        "name": "deel_get_contract",
        "description": "Get a specific Deel contract by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "contract_id": {"type": "string"},
            },
            "required": ["contract_id"],
        },
    },
    {
        "name": "deel_list_people",
        "description": "List all people (workers) on Deel",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
                "search": {"type": "string", "description": "Search by name or email"},
            },
        },
    },
    {
        "name": "deel_list_invoices",
        "description": "List Deel invoices for contractors",
        "parameters": {
            "type": "object",
            "properties": {
                "contract_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "processing", "paid", "failed"],
                },
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "deel_list_time_off",
        "description": "List time-off requests on Deel",
        "parameters": {
            "type": "object",
            "properties": {
                "contract_id": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "approved", "rejected"]},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "deel_create_off_cycle_payment",
        "description": "Create an off-cycle payment for a contractor",
        "parameters": {
            "type": "object",
            "properties": {
                "contract_id": {"type": "string"},
                "amount": {"type": "number"},
                "currency": {"type": "string", "description": "ISO 4217 currency code, e.g. 'USD'"},
                "description": {"type": "string"},
                "payment_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["contract_id", "amount", "currency"],
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("DEEL_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("DEEL_API_KEY", "")
    if not key:
        return {"error": "DEEL_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(base_url=DEEL_BASE, headers=_headers(), timeout=30.0) as c:
            if tool_name == "deel_list_contracts":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 50),
                    "offset": arguments.get("offset", 0),
                }
                if status := arguments.get("status", "active"):
                    if status != "all":
                        params["status"] = status
                if ctype := arguments.get("type"):
                    params["type"] = ctype
                r = await c.get("/contracts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "deel_get_contract":
                r = await c.get(f"/contracts/{arguments['contract_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "deel_list_people":
                params = {
                    "limit": arguments.get("limit", 50),
                    "offset": arguments.get("offset", 0),
                }
                if search := arguments.get("search"):
                    params["search"] = search
                r = await c.get("/people", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "deel_list_invoices":
                params = {"limit": arguments.get("limit", 50)}
                if cid := arguments.get("contract_id"):
                    params["contractId"] = cid
                if status := arguments.get("status"):
                    params["status"] = status
                r = await c.get("/invoices", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "deel_list_time_off":
                params = {}
                if cid := arguments.get("contract_id"):
                    params["contractId"] = cid
                if status := arguments.get("status"):
                    params["status"] = status
                if start := arguments.get("start_date"):
                    params["startDate"] = start
                if end := arguments.get("end_date"):
                    params["endDate"] = end
                r = await c.get("/time-offs", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "deel_create_off_cycle_payment":
                payload: dict[str, Any] = {
                    "contractId": arguments["contract_id"],
                    "amount": arguments["amount"],
                    "currency": arguments["currency"],
                }
                if desc := arguments.get("description"):
                    payload["description"] = desc
                if pd := arguments.get("payment_date"):
                    payload["paymentDate"] = pd
                r = await c.post("/payments/off-cycle", json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("deel_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
