"""DrChrono EHR MCP server — patient management, appointments, and clinical notes.

Environment:
  DRCHRONO_ACCESS_TOKEN: DrChrono OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://drchrono.com/api"

TOOL_DEFINITIONS = [
    {
        "name": "drchrono_list_patients",
        "description": "List patients in DrChrono with optional search filters",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Search by name or chart ID"},
                "doctor": {"type": "integer", "description": "Filter by doctor ID"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "drchrono_create_patient",
        "description": "Create a new patient record in DrChrono",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string", "description": "Patient first name"},
                "last_name": {"type": "string", "description": "Patient last name"},
                "date_of_birth": {"type": "string", "description": "DOB in YYYY-MM-DD format"},
                "gender": {"type": "string", "description": "Patient gender: Male, Female, Other"},
                "email": {"type": "string", "description": "Patient email address"},
                "doctor": {"type": "integer", "description": "Assigned doctor ID"},
            },
            "required": ["first_name", "last_name", "date_of_birth"],
        },
    },
    {
        "name": "drchrono_list_appointments",
        "description": "List appointments in DrChrono filtered by date or patient",
        "parameters": {
            "type": "object",
            "properties": {
                "patient": {"type": "integer", "description": "Filter by patient ID"},
                "doctor": {"type": "integer", "description": "Filter by doctor ID"},
                "date_range": {"type": "string", "description": "Date range in format YYYY-MM-DD/YYYY-MM-DD"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "drchrono_create_appointment",
        "description": "Schedule a new appointment in DrChrono",
        "parameters": {
            "type": "object",
            "properties": {
                "patient": {"type": "integer", "description": "Patient ID"},
                "doctor": {"type": "integer", "description": "Doctor ID"},
                "office": {"type": "integer", "description": "Office/location ID"},
                "scheduled_time": {"type": "string", "description": "Appointment time in ISO 8601 format"},
                "duration": {"type": "integer", "description": "Duration in minutes"},
                "exam_room": {"type": "integer", "description": "Exam room number"},
            },
            "required": ["patient", "doctor", "scheduled_time"],
        },
    },
    {
        "name": "drchrono_list_clinical_notes",
        "description": "List clinical notes (SOAP notes) for a patient or doctor",
        "parameters": {
            "type": "object",
            "properties": {
                "patient": {"type": "integer", "description": "Filter by patient ID"},
                "doctor": {"type": "integer", "description": "Filter by doctor ID"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "drchrono_get_vital_signs",
        "description": "Get vital sign measurements recorded for a patient",
        "parameters": {
            "type": "object",
            "properties": {
                "patient": {"type": "integer", "description": "Patient ID to get vitals for"},
                "appointment": {"type": "integer", "description": "Filter by specific appointment"},
            },
            "required": ["patient"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("DRCHRONO_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "DRCHRONO_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "drchrono_list_patients":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/patients", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "drchrono_create_patient":
                payload = {
                    "first_name": arguments["first_name"],
                    "last_name": arguments["last_name"],
                    "date_of_birth": arguments["date_of_birth"],
                }
                for k in ("gender", "email", "doctor"):
                    if k in arguments:
                        payload[k] = arguments[k]
                r = await client.post(f"{BASE_URL}/patients", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "drchrono_list_appointments":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/appointments", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "drchrono_create_appointment":
                payload = {
                    "patient": arguments["patient"],
                    "doctor": arguments["doctor"],
                    "scheduled_time": arguments["scheduled_time"],
                }
                for k in ("office", "duration", "exam_room"):
                    if k in arguments:
                        payload[k] = arguments[k]
                r = await client.post(f"{BASE_URL}/appointments", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "drchrono_list_clinical_notes":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/clinical_note_templates", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "drchrono_get_vital_signs":
                params = {"patient": arguments["patient"]}
                if "appointment" in arguments:
                    params["appointment"] = arguments["appointment"]
                r = await client.get(f"{BASE_URL}/vitals", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
