"""ProfitWell MCP server — subscription metrics, MRR, churn, and customer analytics.

Environment:
  PROFITWELL_API_KEY: ProfitWell API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://api.profitwell.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "profitwell_get_metrics",
        "description": "Get overall subscription metrics for a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "period_start": {"type": "string", "description": "YYYY-MM-DD start of period"},
                "period_end": {"type": "string", "description": "YYYY-MM-DD end of period"},
            },
        },
    },
    {
        "name": "profitwell_get_mrr",
        "description": "Get Monthly Recurring Revenue (MRR) data",
        "parameters": {
            "type": "object",
            "properties": {
                "period_start": {"type": "string", "description": "YYYY-MM-DD"},
                "period_end": {"type": "string", "description": "YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "profitwell_get_churn",
        "description": "Get churn metrics including customer and revenue churn",
        "parameters": {
            "type": "object",
            "properties": {
                "period_start": {"type": "string"},
                "period_end": {"type": "string"},
            },
        },
    },
    {
        "name": "profitwell_list_customers",
        "description": "List subscription customers",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "churned", "trial"],
                    "default": "active",
                },
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "profitwell_get_customer",
        "description": "Get a specific customer's subscription data",
        "parameters": {
            "type": "object",
            "properties": {
                "user_alias": {"type": "string", "description": "Customer identifier/email"},
            },
            "required": ["user_alias"],
        },
    },
    {
        "name": "profitwell_get_plan_metrics",
        "description": "Get metrics broken down by subscription plan",
        "parameters": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Specific plan ID to filter by"},
                "period_start": {"type": "string"},
                "period_end": {"type": "string"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("PROFITWELL_API_KEY", "")
    if not api_key:
        return {"error": "PROFITWELL_API_KEY not configured"}

    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "profitwell_get_metrics":
                params: dict[str, Any] = {}
                if ps := arguments.get("period_start"):
                    params["period_start"] = ps
                if pe := arguments.get("period_end"):
                    params["period_end"] = pe
                r = await c.get(f"{BASE}/metrics/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "profitwell_get_mrr":
                params = {}
                if ps := arguments.get("period_start"):
                    params["period_start"] = ps
                if pe := arguments.get("period_end"):
                    params["period_end"] = pe
                r = await c.get(f"{BASE}/metrics/mrr/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "profitwell_get_churn":
                params = {}
                if ps := arguments.get("period_start"):
                    params["period_start"] = ps
                if pe := arguments.get("period_end"):
                    params["period_end"] = pe
                r = await c.get(f"{BASE}/metrics/churn/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "profitwell_list_customers":
                params = {
                    "status": arguments.get("status", "active"),
                    "limit": arguments.get("limit", 100),
                    "offset": arguments.get("offset", 0),
                }
                r = await c.get(f"{BASE}/customers/", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "profitwell_get_customer":
                alias = arguments["user_alias"]
                r = await c.get(f"{BASE}/customers/", params={"user_alias": alias})
                r.raise_for_status()
                return r.json()

            elif tool_name == "profitwell_get_plan_metrics":
                params = {}
                if pid := arguments.get("plan_id"):
                    params["plan_id"] = pid
                if ps := arguments.get("period_start"):
                    params["period_start"] = ps
                if pe := arguments.get("period_end"):
                    params["period_end"] = pe
                r = await c.get(f"{BASE}/metrics/plans/", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("profitwell_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
