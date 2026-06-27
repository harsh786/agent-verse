"""BambooHR MCP server — HR employee data, time-off, and org structure.

Environment:
  BAMBOOHR_API_KEY:    API key (used as HTTP Basic username, password='x')
  BAMBOOHR_SUBDOMAIN: Company subdomain (e.g. 'mycompany')
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    subdomain = os.getenv("BAMBOOHR_SUBDOMAIN", "")
    return f"https://api.bamboohr.com/api/gateway.php/{subdomain}/v1"


def _headers() -> dict[str, str]:
    api_key = os.getenv("BAMBOOHR_API_KEY", "")
    creds = base64.b64encode(f"{api_key}:x".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


TOOL_DEFINITIONS = [
    {
        "name": "bamboo_get_employee",
        "description": "Get a BambooHR employee by ID with selected fields",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "string", "description": "Employee ID or 'self'"},
                "fields": {
                    "type": "string",
                    "description": "Comma-separated field names, e.g. 'firstName,lastName,jobTitle,department'",
                    "default": "firstName,lastName,jobTitle,department,workEmail,hireDate",
                },
            },
            "required": ["employee_id"],
        },
    },
    {
        "name": "bamboo_list_employees",
        "description": "List all employees from BambooHR employee directory",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "bamboo_update_employee",
        "description": "Update a BambooHR employee's fields",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
                "fields": {
                    "type": "object",
                    "description": "Fields to update, e.g. {'jobTitle': 'Senior Engineer'}",
                },
            },
            "required": ["employee_id", "fields"],
        },
    },
    {
        "name": "bamboo_get_time_off",
        "description": "Get time-off requests, optionally filtered by employee or date range",
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end": {"type": "string", "description": "End date YYYY-MM-DD"},
                "employee_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["approved", "denied", "superceded", "requested", "canceled"],
                },
            },
        },
    },
    {
        "name": "bamboo_request_time_off",
        "description": "Create a time-off request for an employee",
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {"type": "string"},
                "time_off_type_id": {"type": "integer"},
                "start": {"type": "string", "description": "YYYY-MM-DD"},
                "end": {"type": "string", "description": "YYYY-MM-DD"},
                "note": {"type": "string"},
            },
            "required": ["employee_id", "time_off_type_id", "start", "end"],
        },
    },
    {
        "name": "bamboo_list_departments",
        "description": "List all departments defined in BambooHR",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("BAMBOOHR_API_KEY", "")
    subdomain = os.getenv("BAMBOOHR_SUBDOMAIN", "")
    if not api_key or not subdomain:
        return {"error": "BAMBOOHR_API_KEY and BAMBOOHR_SUBDOMAIN must be configured"}

    base = _base()

    try:
        async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as c:
            if tool_name == "bamboo_get_employee":
                eid = arguments["employee_id"]
                fields = arguments.get("fields", "firstName,lastName,jobTitle,department,workEmail,hireDate")
                r = await c.get(f"{base}/employees/{eid}", params={"fields": fields})
                r.raise_for_status()
                return r.json()

            elif tool_name == "bamboo_list_employees":
                r = await c.get(f"{base}/employees/directory")
                r.raise_for_status()
                data = r.json()
                return {
                    "employees": data.get("employees", []),
                    "fields": data.get("fields", []),
                }

            elif tool_name == "bamboo_update_employee":
                eid = arguments["employee_id"]
                r = await c.post(f"{base}/employees/{eid}", json=arguments["fields"])
                r.raise_for_status()
                return {"success": True, "status_code": r.status_code}

            elif tool_name == "bamboo_get_time_off":
                params: dict[str, Any] = {}
                if s := arguments.get("start"):
                    params["start"] = s
                if e := arguments.get("end"):
                    params["end"] = e
                if eid := arguments.get("employee_id"):
                    params["employeeId"] = eid
                if status := arguments.get("status"):
                    params["status"] = status
                r = await c.get(f"{base}/time_off/requests/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "bamboo_request_time_off":
                payload = {
                    "dates": [
                        {
                            "ymd": arguments["start"],
                            "amount": "8",
                            "type": "hours",
                            "hours": "8",
                        }
                    ],
                    "timeOffTypeId": arguments["time_off_type_id"],
                    "status": "requested",
                }
                if note := arguments.get("note"):
                    payload["note"] = note
                eid = arguments["employee_id"]
                r = await c.post(f"{base}/employees/{eid}/time_off/requests", json=payload)
                r.raise_for_status()
                return {"success": True, "status_code": r.status_code}

            elif tool_name == "bamboo_list_departments":
                r = await c.get(f"{base}/lists/department")
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("bamboohr_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
