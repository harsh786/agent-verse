"""Epic EHR FHIR R4 MCP server — access patient data via FHIR R4 API.

Environment:
  EPIC_ACCESS_TOKEN: Epic FHIR OAuth2 access token
  EPIC_BASE_URL: Base URL for the Epic FHIR endpoint (e.g. https://fhir.epic.com/interconnect-fhir-oauth)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    base = os.getenv("EPIC_BASE_URL", "https://fhir.epic.com/interconnect-fhir-oauth")
    return f"{base.rstrip('/')}/api/FHIR/R4"


TOOL_DEFINITIONS = [
    {
        "name": "epic_get_patient",
        "description": "Get FHIR Patient resource by ID including demographics and identifiers",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "FHIR Patient resource ID"},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "epic_list_appointments",
        "description": "List appointments for a patient from the Epic EHR",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "FHIR Patient ID"},
                "date_from": {"type": "string", "description": "Start date filter (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "End date filter (YYYY-MM-DD)"},
                "status": {"type": "string", "description": "Appointment status filter"},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "epic_list_conditions",
        "description": "List active and historical conditions for a patient",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "FHIR Patient ID"},
                "clinical_status": {"type": "string", "description": "Filter by status: active, inactive, resolved"},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "epic_list_medications",
        "description": "List current and historical medication requests for a patient",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "FHIR Patient ID"},
                "status": {"type": "string", "description": "Medication status: active, stopped, completed"},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "epic_get_lab_results",
        "description": "Get laboratory observation results for a patient",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "FHIR Patient ID"},
                "category": {"type": "string", "description": "Observation category (e.g. laboratory)"},
                "date_from": {"type": "string", "description": "Start date filter"},
                "date_to": {"type": "string", "description": "End date filter"},
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "epic_list_care_plans",
        "description": "List care plans for a patient including goals and activities",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "FHIR Patient ID"},
                "status": {"type": "string", "description": "Care plan status: active, completed"},
            },
            "required": ["patient_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("EPIC_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "EPIC_ACCESS_TOKEN not configured"}

    base_url = _base_url()
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/fhir+json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "epic_get_patient":
                r = await client.get(
                    f"{base_url}/Patient/{arguments['patient_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "epic_list_appointments":
                params: dict[str, Any] = {"patient": arguments["patient_id"]}
                if "date_from" in arguments:
                    params["date"] = f"ge{arguments['date_from']}"
                if "date_to" in arguments:
                    params["date"] = params.get("date", "") + f"&date=le{arguments['date_to']}"
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(f"{base_url}/Appointment", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "epic_list_conditions":
                params = {"patient": arguments["patient_id"]}
                if "clinical_status" in arguments:
                    params["clinical-status"] = arguments["clinical_status"]
                r = await client.get(f"{base_url}/Condition", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "epic_list_medications":
                params = {"patient": arguments["patient_id"]}
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(f"{base_url}/MedicationRequest", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "epic_get_lab_results":
                params = {
                    "patient": arguments["patient_id"],
                    "category": arguments.get("category", "laboratory"),
                }
                if "date_from" in arguments:
                    params["date"] = f"ge{arguments['date_from']}"
                if "date_to" in arguments:
                    params["date"] = f"le{arguments['date_to']}"
                r = await client.get(f"{base_url}/Observation", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "epic_list_care_plans":
                params = {"patient": arguments["patient_id"]}
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(f"{base_url}/CarePlan", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
