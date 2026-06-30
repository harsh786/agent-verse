"""Zoho Desk MCP server — customer support ticketing and helpdesk management.

Environment:
  ZOHO_DESK_ACCESS_TOKEN: Zoho Desk OAuth2 access token
  ZOHO_DESK_ORG_ID: Zoho Desk organization ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://desk.zoho.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "zoho_desk_list_tickets",
        "description": "List support tickets with optional status and priority filters",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: Open, On Hold, Escalated, Closed"},
                "priority": {"type": "string", "description": "Filter by priority: Low, Medium, High, Urgent"},
                "department_id": {"type": "string", "description": "Filter by department"},
                "from_index": {"type": "integer", "description": "Pagination offset"},
                "limit": {"type": "integer", "description": "Maximum tickets to return (max 100)"},
                "sort_by": {"type": "string", "description": "Sort field (e.g. createdTime)"},
            },
        },
    },
    {
        "name": "zoho_desk_create_ticket",
        "description": "Create a new support ticket in Zoho Desk",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Subject line of the ticket"},
                "description": {"type": "string", "description": "Detailed description of the issue"},
                "contact_id": {"type": "string", "description": "ID of the contact submitting the ticket"},
                "department_id": {"type": "string", "description": "Department to assign the ticket to"},
                "priority": {"type": "string", "description": "Ticket priority: Low, Medium, High, Urgent"},
                "channel": {"type": "string", "description": "Support channel: Email, Chat, Phone, etc."},
            },
            "required": ["subject"],
        },
    },
    {
        "name": "zoho_desk_update_ticket",
        "description": "Update an existing support ticket status, assignee, or other fields",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "ID of the ticket to update"},
                "status": {"type": "string", "description": "New status for the ticket"},
                "priority": {"type": "string", "description": "New priority level"},
                "assignee_id": {"type": "string", "description": "Agent ID to assign to"},
                "resolution": {"type": "string", "description": "Resolution text for closing"},
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "zoho_desk_list_agents",
        "description": "List all support agents in the Zoho Desk account",
        "parameters": {
            "type": "object",
            "properties": {
                "from_index": {"type": "integer", "description": "Pagination offset"},
                "limit": {"type": "integer", "description": "Maximum agents to return"},
                "department_id": {"type": "string", "description": "Filter by department"},
            },
        },
    },
    {
        "name": "zoho_desk_list_departments",
        "description": "List all departments configured in Zoho Desk",
        "parameters": {
            "type": "object",
            "properties": {
                "from_index": {"type": "integer", "description": "Pagination offset"},
                "limit": {"type": "integer", "description": "Maximum departments to return"},
            },
        },
    },
    {
        "name": "zoho_desk_get_ticket_stats",
        "description": "Get summary statistics on tickets by status, channel, and time period",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date in YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "End date in YYYY-MM-DD"},
                "department_id": {"type": "string", "description": "Filter by department ID"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("ZOHO_DESK_ACCESS_TOKEN", "")
    org_id = os.getenv("ZOHO_DESK_ORG_ID", "")
    if not access_token:
        return {"error": "ZOHO_DESK_ACCESS_TOKEN not configured"}
    if not org_id:
        return {"error": "ZOHO_DESK_ORG_ID not configured"}

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "orgId": org_id,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "zoho_desk_list_tickets":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/tickets", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "zoho_desk_create_ticket":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/tickets", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "zoho_desk_update_ticket":
                ticket_id = arguments["ticket_id"]
                payload = {k: v for k, v in arguments.items() if k != "ticket_id" and v is not None}
                r = await client.patch(
                    f"{BASE_URL}/tickets/{ticket_id}",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "zoho_desk_list_agents":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/agents", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "zoho_desk_list_departments":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/departments", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "zoho_desk_get_ticket_stats":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/reports/ticketSummary", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
