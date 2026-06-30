"""Buildium MCP server — property management, leases, tenants, and financials.

Environment:
  BUILDIUM_CLIENT_ID: Buildium API client ID
  BUILDIUM_CLIENT_SECRET: Buildium API client secret
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.buildium.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "buildium_list_properties",
        "description": "List rental properties managed in Buildium with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Property status: Active, InActive"},
                "page": {"type": "integer", "description": "Page number"},
                "pagesize": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "buildium_list_tenants",
        "description": "List tenants in Buildium with optional property and status filters",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "pagesize": {"type": "integer", "description": "Tenants per page"},
                "email": {"type": "string", "description": "Search by tenant email"},
                "phone": {"type": "string", "description": "Search by tenant phone number"},
            },
        },
    },
    {
        "name": "buildium_list_leases",
        "description": "List rental leases in Buildium with optional status filters",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Lease status: Active, Past, Future"},
                "page": {"type": "integer", "description": "Page number"},
                "pagesize": {"type": "integer", "description": "Leases per page"},
                "property_id": {"type": "integer", "description": "Filter by property ID"},
            },
        },
    },
    {
        "name": "buildium_get_owner_statement",
        "description": "Get owner financial statement for a property within a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "owner_id": {"type": "integer", "description": "Owner ID to get statement for"},
                "start_date": {"type": "string", "description": "Statement start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "Statement end date YYYY-MM-DD"},
            },
            "required": ["owner_id"],
        },
    },
    {
        "name": "buildium_list_maintenance_requests",
        "description": "List maintenance and work order requests with optional status filter",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Request status: New, InProgress, Closed"},
                "property_id": {"type": "integer", "description": "Filter by property ID"},
                "page": {"type": "integer", "description": "Page number"},
                "pagesize": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "buildium_get_financial_summary",
        "description": "Get a financial summary of income and expenses for the portfolio",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Summary start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "Summary end date YYYY-MM-DD"},
                "property_id": {"type": "integer", "description": "Filter by specific property"},
            },
            "required": ["start_date", "end_date"],
        },
    },
]


async def _get_token(client: httpx.AsyncClient) -> str:
    client_id = os.getenv("BUILDIUM_CLIENT_ID", "")
    client_secret = os.getenv("BUILDIUM_CLIENT_SECRET", "")
    return client_id  # Buildium uses API key auth (client_id as key)


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    client_id = os.getenv("BUILDIUM_CLIENT_ID", "")
    client_secret = os.getenv("BUILDIUM_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return {"error": "BUILDIUM_CLIENT_ID and BUILDIUM_CLIENT_SECRET not configured"}

    headers = {
        "x-buildium-client-id": client_id,
        "x-buildium-client-secret": client_secret,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "buildium_list_properties":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/rentals", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "buildium_list_tenants":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/leases/tenants", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "buildium_list_leases":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/leases", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "buildium_get_owner_statement":
                params: dict[str, Any] = {"ownerId": arguments["owner_id"]}
                if "start_date" in arguments:
                    params["startDate"] = arguments["start_date"]
                if "end_date" in arguments:
                    params["endDate"] = arguments["end_date"]
                r = await client.get(f"{BASE_URL}/reports/ownerstatement", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "buildium_list_maintenance_requests":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/maintenancerequests", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "buildium_get_financial_summary":
                params = {
                    "startDate": arguments["start_date"],
                    "endDate": arguments["end_date"],
                }
                if "property_id" in arguments:
                    params["propertyId"] = arguments["property_id"]
                r = await client.get(f"{BASE_URL}/reports/income", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
