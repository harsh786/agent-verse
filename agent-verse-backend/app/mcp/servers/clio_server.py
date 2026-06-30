"""Clio MCP server — legal practice management, matters, contacts, and time tracking.

Environment:
  CLIO_ACCESS_TOKEN: Clio OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://app.clio.com/api/v4"

TOOL_DEFINITIONS = [
    {
        "name": "clio_list_matters",
        "description": "List legal matters in Clio with optional status and client filters",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: open, pending, closed"},
                "client_id": {"type": "integer", "description": "Filter by client ID"},
                "page": {"type": "integer", "description": "Page number"},
                "limit": {"type": "integer", "description": "Results per page"},
                "fields": {"type": "string", "description": "Comma-separated fields to return"},
            },
        },
    },
    {
        "name": "clio_create_matter",
        "description": "Create a new legal matter (case) in Clio",
        "parameters": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Matter description or case name"},
                "client_id": {"type": "integer", "description": "ID of the client this matter belongs to"},
                "practice_area_id": {"type": "integer", "description": "Practice area ID"},
                "status": {"type": "string", "description": "Initial status: open, pending"},
                "open_date": {"type": "string", "description": "Opening date in YYYY-MM-DD format"},
            },
            "required": ["description"],
        },
    },
    {
        "name": "clio_list_contacts",
        "description": "List contacts (clients, opposing counsel, etc.) in Clio",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Contact type: Person or Company"},
                "query": {"type": "string", "description": "Search query for contact name"},
                "page": {"type": "integer", "description": "Page number"},
                "limit": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "clio_create_contact",
        "description": "Create a new contact (person or company) in Clio",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name of the contact"},
                "type": {"type": "string", "description": "Contact type: Person or Company"},
                "email": {"type": "string", "description": "Email address"},
                "phone": {"type": "string", "description": "Phone number"},
                "address": {"type": "object", "description": "Address object with street, city, state, zip"},
            },
            "required": ["name", "type"],
        },
    },
    {
        "name": "clio_list_activities",
        "description": "List billable activities (time entries) in Clio",
        "parameters": {
            "type": "object",
            "properties": {
                "matter_id": {"type": "integer", "description": "Filter by matter ID"},
                "user_id": {"type": "integer", "description": "Filter by user/attorney ID"},
                "start_date": {"type": "string", "description": "Start date filter YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date filter YYYY-MM-DD"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "clio_create_time_entry",
        "description": "Create a time entry for billable work on a matter",
        "parameters": {
            "type": "object",
            "properties": {
                "matter_id": {"type": "integer", "description": "ID of the matter to bill against"},
                "quantity": {"type": "number", "description": "Time quantity in hours"},
                "description": {"type": "string", "description": "Description of work performed"},
                "date": {"type": "string", "description": "Work date in YYYY-MM-DD format"},
                "price": {"type": "number", "description": "Billing rate per hour"},
            },
            "required": ["matter_id", "quantity", "date"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("CLIO_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "CLIO_ACCESS_TOKEN not configured"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "clio_list_matters":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/matters.json", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "clio_create_matter":
                payload: dict[str, Any] = {"data": {k: v for k, v in arguments.items() if v is not None}}
                r = await client.post(f"{BASE_URL}/matters.json", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "clio_list_contacts":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/contacts.json", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "clio_create_contact":
                payload = {"data": {k: v for k, v in arguments.items() if v is not None}}
                r = await client.post(f"{BASE_URL}/contacts.json", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "clio_list_activities":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/activities.json", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "clio_create_time_entry":
                payload = {
                    "data": {
                        "type": "TimeEntry",
                        "quantity": arguments["quantity"],
                        "date": arguments["date"],
                        "matter": {"id": arguments["matter_id"]},
                    }
                }
                if "description" in arguments:
                    payload["data"]["description"] = arguments["description"]
                if "price" in arguments:
                    payload["data"]["price"] = arguments["price"]
                r = await client.post(f"{BASE_URL}/activities.json", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
