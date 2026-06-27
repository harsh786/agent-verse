"""Mixpanel MCP server — query event data, funnels, retention, and user profiles.

Environment:
  MIXPANEL_SERVICE_ACCOUNT_USERNAME: Service account username
  MIXPANEL_SERVICE_ACCOUNT_SECRET:   Service account secret
  MIXPANEL_PROJECT_ID:               Project ID (numeric string)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

MIXPANEL_DATA_BASE = "https://data.mixpanel.com/api/2.0"
MIXPANEL_API_BASE = "https://mixpanel.com/api/2.0"

TOOL_DEFINITIONS = [
    {
        "name": "mixpanel_query_events",
        "description": "Export raw event data from Mixpanel for a date range",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "event": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Event names to filter (omit for all events)",
                },
                "where": {"type": "string", "description": "Filter expression (MQL)"},
            },
            "required": ["from_date", "to_date"],
        },
    },
    {
        "name": "mixpanel_query_funnels",
        "description": "Query Mixpanel funnel conversion data",
        "parameters": {
            "type": "object",
            "properties": {
                "funnel_id": {"type": "integer", "description": "Funnel ID"},
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
                "unit": {
                    "type": "string",
                    "enum": ["day", "week", "month"],
                    "default": "day",
                },
                "interval": {"type": "integer"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["from_date", "to_date"],
        },
    },
    {
        "name": "mixpanel_query_retention",
        "description": "Query Mixpanel retention data (born/returned events)",
        "parameters": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
                "born_event": {"type": "string", "description": "First event name"},
                "event": {"type": "string", "description": "Retention event name"},
                "retention_type": {
                    "type": "string",
                    "enum": ["birth", "compounded"],
                    "default": "birth",
                },
                "unit": {
                    "type": "string",
                    "enum": ["day", "week", "month"],
                    "default": "day",
                },
            },
            "required": ["from_date", "to_date"],
        },
    },
    {
        "name": "mixpanel_user_profile",
        "description": "Query Mixpanel user profiles with optional filters",
        "parameters": {
            "type": "object",
            "properties": {
                "distinct_id": {"type": "string", "description": "Specific user distinct ID"},
                "where": {"type": "string", "description": "Filter expression (MQL)"},
                "limit": {"type": "integer", "default": 100},
                "session_id": {"type": "string", "description": "Pagination session ID"},
                "page": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "mixpanel_query_segmentation",
        "description": "Get segmented event counts over time",
        "parameters": {
            "type": "object",
            "properties": {
                "event": {"type": "string", "description": "Event name"},
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
                "unit": {
                    "type": "string",
                    "enum": ["minute", "hour", "day", "week", "month"],
                    "default": "day",
                },
                "on": {"type": "string", "description": "Property to segment by"},
                "where": {"type": "string", "description": "Filter expression"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["event", "from_date", "to_date"],
        },
    },
]


def _auth() -> tuple[str, str]:
    return (
        os.getenv("MIXPANEL_SERVICE_ACCOUNT_USERNAME", ""),
        os.getenv("MIXPANEL_SERVICE_ACCOUNT_SECRET", ""),
    )


def _project_id() -> str:
    return os.getenv("MIXPANEL_PROJECT_ID", "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    username, secret = _auth()
    project_id = _project_id()

    if not username or not secret:
        return {"error": "MIXPANEL_SERVICE_ACCOUNT_USERNAME and MIXPANEL_SERVICE_ACCOUNT_SECRET not configured"}
    if not project_id:
        return {"error": "MIXPANEL_PROJECT_ID not configured"}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # All Mixpanel Data API calls use HTTP Basic Auth
            auth = httpx.BasicAuth(username, secret)

            if tool_name == "mixpanel_query_events":
                import json as _json
                params: dict[str, Any] = {
                    "from_date": arguments["from_date"],
                    "to_date": arguments["to_date"],
                    "project_id": project_id,
                }
                if events := arguments.get("event"):
                    params["event"] = _json.dumps(events)
                if where := arguments.get("where"):
                    params["where"] = where
                resp = await client.get(
                    f"{MIXPANEL_DATA_BASE}/export",
                    params=params,
                    auth=auth,
                )
                resp.raise_for_status()
                # Export returns newline-delimited JSON
                lines = [line for line in resp.text.strip().splitlines() if line]
                events_data = []
                for line in lines[:1000]:
                    try:
                        events_data.append(_json.loads(line))
                    except Exception:
                        pass
                return {"events": events_data, "count": len(events_data)}

            elif tool_name == "mixpanel_query_funnels":
                params = {
                    "from_date": arguments["from_date"],
                    "to_date": arguments["to_date"],
                    "project_id": project_id,
                    "unit": arguments.get("unit", "day"),
                    "limit": arguments.get("limit", 5),
                }
                if funnel_id := arguments.get("funnel_id"):
                    params["funnel_id"] = funnel_id
                if interval := arguments.get("interval"):
                    params["interval"] = interval
                resp = await client.get(
                    f"{MIXPANEL_DATA_BASE}/funnels",
                    params=params,
                    auth=auth,
                )
                resp.raise_for_status()
                return resp.json()

            elif tool_name == "mixpanel_query_retention":
                params = {
                    "from_date": arguments["from_date"],
                    "to_date": arguments["to_date"],
                    "project_id": project_id,
                    "retention_type": arguments.get("retention_type", "birth"),
                    "unit": arguments.get("unit", "day"),
                }
                if born_event := arguments.get("born_event"):
                    params["born_event"] = born_event
                if event := arguments.get("event"):
                    params["event"] = event
                resp = await client.get(
                    f"{MIXPANEL_DATA_BASE}/retention",
                    params=params,
                    auth=auth,
                )
                resp.raise_for_status()
                return resp.json()

            elif tool_name == "mixpanel_user_profile":
                params = {
                    "project_id": project_id,
                    "limit": arguments.get("limit", 100),
                    "page": arguments.get("page", 0),
                }
                if distinct_id := arguments.get("distinct_id"):
                    params["distinct_id"] = distinct_id
                if where := arguments.get("where"):
                    params["where"] = where
                if session_id := arguments.get("session_id"):
                    params["session_id"] = session_id
                resp = await client.get(
                    f"{MIXPANEL_API_BASE}/engage",
                    params=params,
                    auth=auth,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "results": data.get("results", []),
                    "session_id": data.get("session_id"),
                    "page": data.get("page"),
                    "total": data.get("total"),
                }

            elif tool_name == "mixpanel_query_segmentation":
                params = {
                    "event": arguments["event"],
                    "from_date": arguments["from_date"],
                    "to_date": arguments["to_date"],
                    "project_id": project_id,
                    "unit": arguments.get("unit", "day"),
                    "limit": arguments.get("limit", 10),
                }
                if on := arguments.get("on"):
                    params["on"] = on
                if where := arguments.get("where"):
                    params["where"] = where
                resp = await client.get(
                    f"{MIXPANEL_DATA_BASE}/segmentation",
                    params=params,
                    auth=auth,
                )
                resp.raise_for_status()
                return resp.json()

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("mixpanel_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
