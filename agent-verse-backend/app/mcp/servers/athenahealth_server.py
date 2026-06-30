"""Athenahealth EHR MCP server — patient records, appointments, and clinical data.

Environment:
  ATHENA_ACCESS_TOKEN: Athenahealth OAuth2 access token
  ATHENA_PRACTICE_ID: Practice ID for the Athenahealth account
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    practice_id = os.getenv("ATHENA_PRACTICE_ID", "")
    return f"https://api.platform.athenahealth.com/v1/{practice_id}"


TOOL_DEFINITIONS = [
    {
        "name": "athena_list_patients",
        "description": "Search for patients in Athenahealth by name, DOB, or other criteria",
        "parameters": {
            "type": "object",
            "properties": {
                "lastname": {"type": "string", "description": "Patient last name"},
                "firstname": {"type": "string", "description": "Patient first name"},
                "dob": {"type": "string", "description": "Date of birth in MM/DD/YYYY format"},
                "limit": {"type": "integer", "description": "Maximum results to return"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "athena_get_patient",
        "description": "Get detailed information for a specific patient by their ID",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "Athenahealth patient ID"},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "athena_list_appointments",
        "description": "List appointments for a patient or within a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "Patient ID to get appointments for"},
                "startdate": {"type": "string", "description": "Start date MM/DD/YYYY"},
                "enddate": {"type": "string", "description": "End date MM/DD/YYYY"},
                "limit": {"type": "integer", "description": "Maximum results"},
            },
        },
    },
    {
        "name": "athena_create_appointment",
        "description": "Create a new appointment for a patient",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "Patient ID"},
                "appointment_id": {"type": "string", "description": "Open appointment slot ID"},
                "reason_id": {"type": "string", "description": "Appointment reason/type ID"},
                "note": {"type": "string", "description": "Appointment notes"},
            },
            "required": ["patient_id", "appointment_id"],
        },
    },
    {
        "name": "athena_list_providers",
        "description": "List healthcare providers in the practice",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum results"},
                "offset": {"type": "integer", "description": "Pagination offset"},
                "showallproviderids": {"type": "boolean", "description": "Include all provider ID types"},
            },
        },
    },
    {
        "name": "athena_get_chart",
        "description": "Get the clinical chart summary for a patient",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "Patient ID"},
                "department_id": {"type": "string", "description": "Department ID for context"},
            },
            "required": ["patient_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("ATHENA_ACCESS_TOKEN", "")
    practice_id = os.getenv("ATHENA_PRACTICE_ID", "")
    if not access_token:
        return {"error": "ATHENA_ACCESS_TOKEN not configured"}
    if not practice_id:
        return {"error": "ATHENA_PRACTICE_ID not configured"}

    base_url = _base_url()
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "athena_list_patients":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{base_url}/patients", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "athena_get_patient":
                r = await client.get(
                    f"{base_url}/patients/{arguments['patient_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "athena_list_appointments":
                params = {k: v for k, v in arguments.items() if k != "patient_id" and v is not None}
                if "patient_id" in arguments:
                    r = await client.get(
                        f"{base_url}/patients/{arguments['patient_id']}/appointments",
                        headers=headers,
                        params=params,
                    )
                else:
                    r = await client.get(f"{base_url}/appointments", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "athena_create_appointment":
                patient_id = arguments["patient_id"]
                payload = {
                    "appointmentid": arguments["appointment_id"],
                    "reasonid": arguments.get("reason_id", ""),
                }
                if "note" in arguments:
                    payload["note"] = arguments["note"]
                r = await client.put(
                    f"{base_url}/patients/{patient_id}/appointments/{arguments['appointment_id']}",
                    headers=headers,
                    data=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "athena_list_providers":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{base_url}/providers", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "athena_get_chart":
                patient_id = arguments["patient_id"]
                params = {}
                if "department_id" in arguments:
                    params["departmentid"] = arguments["department_id"]
                r = await client.get(
                    f"{base_url}/chart/{patient_id}/summary",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
