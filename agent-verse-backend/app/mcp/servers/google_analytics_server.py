"""Google Analytics (GA4) MCP server — reporting and data via Analytics Data API.

Environment variables (one required):
  GOOGLE_ACCESS_TOKEN:         OAuth2 bearer token
  GOOGLE_SERVICE_ACCOUNT_JSON: JSON string of a service-account key file
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

GA4_BASE = "https://analyticsdata.googleapis.com/v1beta"
ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"

TOOL_DEFINITIONS = [
    {
        "name": "ga4_run_report",
        "description": "Run a Google Analytics 4 report for a property",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {
                    "type": "string",
                    "description": "GA4 property ID (numeric, e.g. '123456789')",
                },
                "dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Dimension names e.g. ['date', 'country', 'sessionSource']",
                },
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Metric names e.g. ['sessions', 'activeUsers', 'bounceRate']",
                },
                "date_ranges": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Date range objects with 'startDate' and 'endDate' (YYYY-MM-DD or 'today','7daysAgo')",
                    "default": [{"startDate": "7daysAgo", "endDate": "today"}],
                },
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
                "dimension_filter": {
                    "type": "object",
                    "description": "Optional dimension filter expression",
                },
            },
            "required": ["property_id", "metrics"],
        },
    },
    {
        "name": "ga4_run_realtime_report",
        "description": "Get real-time active user data for a GA4 property",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["activeUsers"],
                },
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["property_id"],
        },
    },
    {
        "name": "ga4_list_properties",
        "description": "List all GA4 properties under an account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "GA4 account ID"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "ga4_get_metadata",
        "description": "Get all available dimensions and metrics for a GA4 property",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
            },
            "required": ["property_id"],
        },
    },
    {
        "name": "ga4_run_funnel_report",
        "description": "Run a funnel report to analyze user journeys",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "funnel_steps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Funnel step objects with 'name' and 'filterExpression'",
                },
                "date_ranges": {
                    "type": "array",
                    "items": {"type": "object"},
                    "default": [{"startDate": "30daysAgo", "endDate": "today"}],
                },
            },
            "required": ["property_id", "funnel_steps"],
        },
    },
    {
        "name": "ga4_get_audience_overview",
        "description": "Get audience overview metrics (users, sessions, pageviews, bounce rate) for a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "start_date": {"type": "string", "default": "28daysAgo"},
                "end_date": {"type": "string", "default": "today"},
            },
            "required": ["property_id"],
        },
    },
]


def _google_token() -> str:
    direct = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if direct:
        return direct
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if sa_json:
        try:
            from google.auth.transport.requests import Request  # type: ignore[import]
            from google.oauth2 import service_account  # type: ignore[import]

            creds = service_account.Credentials.from_service_account_info(
                json.loads(sa_json),
                scopes=["https://www.googleapis.com/auth/analytics.readonly"],
            )
            creds.refresh(Request())
            return creds.token  # type: ignore[return-value]
        except Exception:
            logger.debug("google_service_account_refresh_failed", exc_info=True)
    return ""


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = _google_token()
    if not token:
        return {"error": "GOOGLE_ACCESS_TOKEN or GOOGLE_SERVICE_ACCOUNT_JSON required"}

    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    pid = arguments.get("property_id", "")

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "ga4_run_report":
                body: dict[str, Any] = {
                    "dateRanges": arguments.get("date_ranges", [{"startDate": "7daysAgo", "endDate": "today"}]),
                    "metrics": [{"name": m} for m in arguments.get("metrics", [])],
                    "limit": arguments.get("limit", 100),
                    "offset": arguments.get("offset", 0),
                }
                if dims := arguments.get("dimensions"):
                    body["dimensions"] = [{"name": d} for d in dims]
                if df := arguments.get("dimension_filter"):
                    body["dimensionFilter"] = df
                r = await c.post(
                    f"{GA4_BASE}/properties/{pid}:runReport",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "ga4_run_realtime_report":
                body = {
                    "metrics": [{"name": m} for m in arguments.get("metrics", ["activeUsers"])],
                    "limit": arguments.get("limit", 10),
                }
                if dims := arguments.get("dimensions"):
                    body["dimensions"] = [{"name": d} for d in dims]
                r = await c.post(
                    f"{GA4_BASE}/properties/{pid}:runRealtimeReport",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "ga4_list_properties":
                account_id = arguments["account_id"]
                r = await c.get(
                    f"{ADMIN_BASE}/properties",
                    headers=hdrs,
                    params={"filter": f"ancestor:accounts/{account_id}"},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "properties": [
                        {"name": p["name"], "display_name": p.get("displayName", ""),
                         "industry": p.get("industryCategory", ""), "time_zone": p.get("timeZone", "")}
                        for p in data.get("properties", [])
                    ]
                }

            elif tool_name == "ga4_get_metadata":
                r = await c.get(
                    f"{GA4_BASE}/properties/{pid}/metadata",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "dimensions": [
                        {"api_name": d["apiName"], "ui_name": d.get("uiName", "")}
                        for d in data.get("dimensions", [])
                    ],
                    "metrics": [
                        {"api_name": m["apiName"], "ui_name": m.get("uiName", ""),
                         "type": m.get("type", "")}
                        for m in data.get("metrics", [])
                    ],
                }

            elif tool_name == "ga4_run_funnel_report":
                body = {
                    "dateRanges": arguments.get("date_ranges", [{"startDate": "30daysAgo", "endDate": "today"}]),
                    "funnel": {"steps": arguments["funnel_steps"]},
                }
                r = await c.post(
                    f"{GA4_BASE}/properties/{pid}:runFunnelReport",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "ga4_get_audience_overview":
                start = arguments.get("start_date", "28daysAgo")
                end = arguments.get("end_date", "today")
                body = {
                    "dateRanges": [{"startDate": start, "endDate": end}],
                    "metrics": [
                        {"name": "activeUsers"},
                        {"name": "sessions"},
                        {"name": "screenPageViews"},
                        {"name": "bounceRate"},
                        {"name": "averageSessionDuration"},
                        {"name": "newUsers"},
                    ],
                }
                r = await c.post(
                    f"{GA4_BASE}/properties/{pid}:runReport",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("ga4_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
