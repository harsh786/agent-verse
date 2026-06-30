"""Gusto MCP server — payroll, HR, employees, pay periods, and benefits.

Environment:
  GUSTO_ACCESS_TOKEN: Gusto OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.gusto.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "gusto_list_employees",
        "description": "List all employees for a company",
        "parameters": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string", "description": "Gusto company UUID or ID"},
                "terminated": {"type": "boolean", "default": False, "description": "Include terminated employees"},
                "page": {"type": "integer", "default": 1},
                "per": {"type": "integer", "default": 25},
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "gusto_get_employee",
        "description": "Get a specific employee by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
            },
            "required": ["employee_id"],
        },
    },
    {
        "name": "gusto_list_pay_periods",
        "description": "List pay periods for a company",
        "parameters": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "gusto_get_payroll",
        "description": "Get a specific payroll run for a company",
        "parameters": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "payroll_id": {"type": "string"},
            },
            "required": ["company_id", "payroll_id"],
        },
    },
    {
        "name": "gusto_list_benefits",
        "description": "List company benefits",
        "parameters": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
            },
            "required": ["company_id"],
        },
    },
    {
        "name": "gusto_get_company",
        "description": "Get company details",
        "parameters": {
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
            },
            "required": ["company_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("GUSTO_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "GUSTO_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "gusto_list_employees":
                cid = arguments["company_id"]
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per": arguments.get("per", 25),
                }
                if arguments.get("terminated"):
                    params["terminated"] = True
                r = await c.get(f"{BASE}/companies/{cid}/employees", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gusto_get_employee":
                eid = arguments["employee_id"]
                r = await c.get(f"{BASE}/employees/{eid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "gusto_list_pay_periods":
                cid = arguments["company_id"]
                params = {}
                if sd := arguments.get("start_date"):
                    params["start_date"] = sd
                if ed := arguments.get("end_date"):
                    params["end_date"] = ed
                r = await c.get(f"{BASE}/companies/{cid}/pay_periods", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gusto_get_payroll":
                cid = arguments["company_id"]
                pid = arguments["payroll_id"]
                r = await c.get(f"{BASE}/companies/{cid}/payrolls/{pid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "gusto_list_benefits":
                cid = arguments["company_id"]
                r = await c.get(f"{BASE}/companies/{cid}/company_benefits")
                r.raise_for_status()
                return r.json()

            elif tool_name == "gusto_get_company":
                cid = arguments["company_id"]
                r = await c.get(f"{BASE}/companies/{cid}")
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("gusto_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
