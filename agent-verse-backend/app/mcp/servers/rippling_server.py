"""Rippling MCP server — HR, IT, and payroll platform (basic).

Environment:
  RIPPLING_API_KEY: Rippling API key (Bearer token)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

RIPPLING_BASE = "https://app.rippling.com/api/platform/api"

TOOL_DEFINITIONS = [
    {
        "name": "rippling_list_employees",
        "description": "List employees in Rippling",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
                "employment_type": {
                    "type": "string",
                    "enum": ["EMPLOYEE", "CONTRACTOR"],
                },
                "status": {"type": "string", "enum": ["ACTIVE", "INACTIVE", "ALL"], "default": "ACTIVE"},
            },
        },
    },
    {
        "name": "rippling_get_employee",
        "description": "Get a specific Rippling employee by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
            },
            "required": ["employee_id"],
        },
    },
    {
        "name": "rippling_list_departments",
        "description": "List departments in Rippling",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "rippling_list_leave_requests",
        "description": "List leave/time-off requests",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["PENDING", "APPROVED", "DENIED", "CANCELLED"],
                },
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "rippling_get_company",
        "description": "Get company details from Rippling",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


def _headers() -> dict[str, str]:
    key = os.getenv("RIPPLING_API_KEY", "")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = os.getenv("RIPPLING_API_KEY", "")
    if not key:
        return {"error": "RIPPLING_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(base_url=RIPPLING_BASE, headers=_headers(), timeout=30.0) as c:
            if tool_name == "rippling_list_employees":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 100),
                    "offset": arguments.get("offset", 0),
                }
                if emp_type := arguments.get("employment_type"):
                    params["employmentType"] = emp_type
                status = arguments.get("status", "ACTIVE")
                if status != "ALL":
                    params["employmentStatus"] = status
                r = await c.get("/employees", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "rippling_get_employee":
                r = await c.get(f"/employees/{arguments['employee_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "rippling_list_departments":
                r = await c.get("/departments")
                r.raise_for_status()
                return r.json()

            elif tool_name == "rippling_list_leave_requests":
                params = {}
                if eid := arguments.get("employee_id"):
                    params["employeeId"] = eid
                if status := arguments.get("status"):
                    params["status"] = status
                if start := arguments.get("start_date"):
                    params["startDate"] = start
                if end := arguments.get("end_date"):
                    params["endDate"] = end
                r = await c.get("/leave-requests", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "rippling_get_company":
                r = await c.get("/company")
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("rippling_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
