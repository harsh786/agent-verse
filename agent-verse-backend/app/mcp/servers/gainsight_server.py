"""Gainsight MCP server — customer success: accounts, CSMs, CTAs, and scorecards.

Environment variables:
  GAINSIGHT_ACCESS_KEY: Gainsight API access key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GAINSIGHT_BASE = "https://api.gainsight.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "gainsight_list_accounts",
        "description": "List customer accounts in Gainsight with optional filtering and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "pageSize": {"type": "integer", "default": 25},
                "status": {"type": "string", "description": "Filter by account status"},
                "q": {"type": "string", "description": "Search query for account name"},
            },
        },
    },
    {
        "name": "gainsight_get_account",
        "description": "Get detailed information about a specific Gainsight account by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Gainsight account GSID"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "gainsight_update_account_health",
        "description": "Update the health score or status for a Gainsight account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Gainsight account GSID"},
                "healthScore": {"type": "number", "description": "Overall health score (0-100)"},
                "healthStatus": {
                    "type": "string",
                    "enum": ["RED", "YELLOW", "GREEN"],
                    "description": "Health status colour",
                },
                "reason": {"type": "string", "description": "Reason for health change"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "gainsight_list_csms",
        "description": "List Customer Success Managers (CSMs) in Gainsight",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "pageSize": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "gainsight_create_call_to_action",
        "description": "Create a Call to Action (CTA) in Gainsight to trigger a workflow or alert",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account GSID to associate the CTA with"},
                "name": {"type": "string", "description": "CTA name/title"},
                "reason": {"type": "string", "description": "Reason for creating the CTA"},
                "type": {
                    "type": "string",
                    "enum": ["Risk", "Opportunity", "Event"],
                    "default": "Risk",
                },
                "priority": {
                    "type": "string",
                    "enum": ["High", "Medium", "Low"],
                    "default": "Medium",
                },
                "assignee_id": {"type": "string", "description": "User GSID to assign the CTA to"},
                "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)"},
            },
            "required": ["account_id", "name", "reason"],
        },
    },
    {
        "name": "gainsight_get_scorecards",
        "description": "Get scorecard measures and scores for a Gainsight account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account GSID"},
                "scorecard_id": {"type": "string", "description": "Specific scorecard ID (optional)"},
            },
            "required": ["account_id"],
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Accesskey": api_key,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("GAINSIGHT_ACCESS_KEY", "")
    if not api_key:
        return {"error": "GAINSIGHT_ACCESS_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=GAINSIGHT_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "gainsight_list_accounts":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "pageSize": arguments.get("pageSize", 25),
                }
                if q := arguments.get("q"):
                    params["q"] = q
                if status := arguments.get("status"):
                    params["status"] = status
                r = await c.get("/accounts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gainsight_get_account":
                r = await c.get(f"/accounts/{arguments['account_id']}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "gainsight_update_account_health":
                aid = arguments["account_id"]
                body: dict[str, Any] = {}
                for k in ("healthScore", "healthStatus", "reason"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.patch(f"/accounts/{aid}/health", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gainsight_list_csms":
                params = {
                    "page": arguments.get("page", 1),
                    "pageSize": arguments.get("pageSize", 25),
                }
                r = await c.get("/users", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gainsight_create_call_to_action":
                body = {
                    "name": arguments["name"],
                    "reason": arguments["reason"],
                    "accountId": arguments["account_id"],
                    "type": arguments.get("type", "Risk"),
                    "priority": arguments.get("priority", "Medium"),
                }
                if "assignee_id" in arguments:
                    body["assigneeId"] = arguments["assignee_id"]
                if "due_date" in arguments:
                    body["dueDate"] = arguments["due_date"]
                r = await c.post("/ctas", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "gainsight_get_scorecards":
                aid = arguments["account_id"]
                path = f"/accounts/{aid}/scorecards"
                if sid := arguments.get("scorecard_id"):
                    path += f"/{sid}"
                r = await c.get(path)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("gainsight_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
