"""Expensify MCP server — expense management.

Environment:
  EXPENSIFY_PARTNER_USER_ID:     Expensify API partner user ID
  EXPENSIFY_PARTNER_USER_SECRET: Expensify API partner secret
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.mcp.servers.credentials import tenant_getenv
from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "expensify_create_expense",
        "description": "Create a new expense report item in Expensify",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Employee email"},
                "merchant": {"type": "string"},
                "amount": {"type": "number", "description": "Amount in cents"},
                "currency": {"type": "string", "default": "USD"},
                "created": {"type": "string", "description": "Date YYYY-MM-DD"},
                "category": {"type": "string"},
                "comment": {"type": "string"},
            },
            "required": ["email", "merchant", "amount", "created"],
        },
    },
    {
        "name": "expensify_get_reports",
        "description": "Get expense reports for a user or date range",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["OPEN", "SUBMITTED", "APPROVED", "REIMBURSED"],
                    "description": "Filter by status",
                },
            },
        },
    },
    {
        "name": "expensify_approve_report",
        "description": "Approve an expense report",
        "parameters": {
            "type": "object",
            "properties": {
                "report_id": {"type": "string"},
                "approver": {"type": "string", "description": "Approver email"},
            },
            "required": ["report_id", "approver"],
        },
    },
]

_API_URL = "https://integrations.expensify.com/Integration-Server/ExpensifyIntegrations"


async def call_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    # Read per call: on a tenant call these come from the connector only.
    partner_user_id = tenant_getenv("EXPENSIFY_PARTNER_USER_ID", "")
    partner_user_secret = tenant_getenv("EXPENSIFY_PARTNER_USER_SECRET", "")
    if not partner_user_id or not partner_user_secret:
        return {"error": "Expensify partner user ID and secret are not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "expensify_create_expense":
                request_json = {
                    "type": "create",
                    "credentials": {
                        "partnerUserID": partner_user_id,
                        "partnerUserSecret": partner_user_secret,
                    },
                    "inputSettings": {
                        "type": "expenses",
                        "employeeEmail": params["email"],
                        "expenses": [
                            {
                                "merchant": params["merchant"],
                                "amount": int(params["amount"]),
                                "currency": params.get("currency", "USD"),
                                "created": params["created"],
                                "category": params.get("category", ""),
                                "comment": params.get("comment", ""),
                            }
                        ],
                    },
                }
                resp = await client.post(
                    _API_URL,
                    data={"requestJobDescription": json.dumps(request_json)},
                )
                resp.raise_for_status()
                return {"result": resp.text}

            if tool_name == "expensify_get_reports":
                request_json = {
                    "type": "get",
                    "credentials": {
                        "partnerUserID": partner_user_id,
                        "partnerUserSecret": partner_user_secret,
                    },
                    "inputSettings": {
                        "type": "reportInfos",
                        "filters": {
                            "startDate": params.get("start_date", ""),
                            "endDate": params.get("end_date", ""),
                            "email": params.get("email", ""),
                            "status": params.get("status", ""),
                        },
                    },
                }
                resp = await client.post(
                    _API_URL,
                    data={"requestJobDescription": json.dumps(request_json)},
                )
                resp.raise_for_status()
                return {"result": resp.text}

            if tool_name == "expensify_approve_report":
                request_json = {
                    "type": "update",
                    "credentials": {
                        "partnerUserID": partner_user_id,
                        "partnerUserSecret": partner_user_secret,
                    },
                    "inputSettings": {
                        "type": "report",
                        "reportID": params["report_id"],
                        "status": "APPROVED",
                        "approver": params["approver"],
                    },
                }
                resp = await client.post(
                    _API_URL,
                    data={"requestJobDescription": json.dumps(request_json)},
                )
                resp.raise_for_status()
                return {"approved": True, "result": resp.text}

            return {"error": f"Unknown tool: {tool_name}"}

        except Exception as e:
            logger.error("expensify.tool_error", tool=tool_name, error=str(e))
            return {"error": str(e)}
