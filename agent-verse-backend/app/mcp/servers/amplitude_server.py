"""Amplitude MCP server — query events, cohorts, and user profiles.

Environment:
  AMPLITUDE_API_KEY:    Amplitude project API key
  AMPLITUDE_SECRET_KEY: Amplitude project secret key

Auth: HTTP Basic (api_key:secret_key)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

AMPLITUDE_BASE = "https://amplitude.com/api/2"

TOOL_DEFINITIONS = [
    {
        "name": "amplitude_query_events",
        "description": "Query Amplitude event data with segmentation",
        "parameters": {
            "type": "object",
            "properties": {
                "event": {
                    "type": "object",
                    "description": "Event definition (e.g. {event_type: 'Button Click'})",
                },
                "start": {"type": "string", "description": "Start date YYYYMMDD"},
                "end": {"type": "string", "description": "End date YYYYMMDD"},
                "m": {
                    "type": "string",
                    "default": "uniques",
                    "description": "Metric: uniques, totals, pct_dau, average",
                },
                "i": {
                    "type": "integer",
                    "default": 1,
                    "description": "Interval: 1=day, 7=week, 30=month",
                },
                "s": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Segment definitions",
                },
                "g": {
                    "type": "string",
                    "description": "Group by property",
                },
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["event", "start", "end"],
        },
    },
    {
        "name": "amplitude_get_cohort",
        "description": "Get details and membership of an Amplitude cohort",
        "parameters": {
            "type": "object",
            "properties": {
                "cohort_id": {"type": "string"},
                "props": {"type": "integer", "default": 0, "description": "Include user properties (1=yes)"},
                "csv": {"type": "boolean", "default": False, "description": "Return as CSV"},
            },
            "required": ["cohort_id"],
        },
    },
    {
        "name": "amplitude_export_events",
        "description": "Export raw events from Amplitude for a time range",
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Start datetime (YYYYMMDDTHH, e.g. 20230101T00)"},
                "end": {"type": "string", "description": "End datetime (YYYYMMDDTHH, e.g. 20230101T23)"},
            },
            "required": ["start", "end"],
        },
    },
    {
        "name": "amplitude_user_profile",
        "description": "Get Amplitude user profile and recent events",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User ID"},
                "amplitude_id": {"type": "integer", "description": "Amplitude user ID (alternative to user_id)"},
                "get_events": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 1000},
            },
        },
    },
    {
        "name": "amplitude_list_cohorts",
        "description": "List all cohorts in the Amplitude project",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


def _auth() -> httpx.BasicAuth:
    return httpx.BasicAuth(
        os.getenv("AMPLITUDE_API_KEY", ""),
        os.getenv("AMPLITUDE_SECRET_KEY", ""),
    )


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("AMPLITUDE_API_KEY", "")
    secret_key = os.getenv("AMPLITUDE_SECRET_KEY", "")

    if not api_key or not secret_key:
        return {"error": "AMPLITUDE_API_KEY and AMPLITUDE_SECRET_KEY not configured"}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            auth = _auth()

            if tool_name == "amplitude_query_events":
                import json as _json
                params: dict[str, Any] = {
                    "e": _json.dumps(arguments["event"]),
                    "start": arguments["start"],
                    "end": arguments["end"],
                    "m": arguments.get("m", "uniques"),
                    "i": arguments.get("i", 1),
                    "limit": arguments.get("limit", 100),
                }
                if segs := arguments.get("s"):
                    params["s"] = _json.dumps(segs)
                if group := arguments.get("g"):
                    params["g"] = group
                resp = await client.get(
                    f"{AMPLITUDE_BASE}/events/segmentation",
                    params=params,
                    auth=auth,
                )
                resp.raise_for_status()
                return resp.json()

            elif tool_name == "amplitude_get_cohort":
                cohort_id = arguments["cohort_id"]
                params = {
                    "props": arguments.get("props", 0),
                }
                if arguments.get("csv"):
                    params["output_format"] = "csv"
                resp = await client.get(
                    f"{AMPLITUDE_BASE}/cohorts/{cohort_id}",
                    params=params,
                    auth=auth,
                )
                resp.raise_for_status()
                return resp.json()

            elif tool_name == "amplitude_export_events":
                resp = await client.get(
                    f"{AMPLITUDE_BASE}/export",
                    params={"start": arguments["start"], "end": arguments["end"]},
                    auth=auth,
                )
                resp.raise_for_status()
                # Export returns a zip; return metadata about the response
                return {
                    "content_type": resp.headers.get("content-type", ""),
                    "content_length": len(resp.content),
                    "note": "Raw export data received. Parse as zip containing newline-delimited JSON files.",
                }

            elif tool_name == "amplitude_user_profile":
                params = {
                    "limit": arguments.get("limit", 1000),
                    "get_events": str(arguments.get("get_events", True)).lower(),
                }
                if user_id := arguments.get("user_id"):
                    params["user_id"] = user_id
                elif amp_id := arguments.get("amplitude_id"):
                    params["amplitude_id"] = amp_id
                else:
                    return {"error": "Either user_id or amplitude_id must be provided"}
                resp = await client.get(
                    f"{AMPLITUDE_BASE}/userprofile",
                    params=params,
                    auth=auth,
                )
                resp.raise_for_status()
                return resp.json()

            elif tool_name == "amplitude_list_cohorts":
                resp = await client.get(
                    f"{AMPLITUDE_BASE}/cohorts",
                    auth=auth,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "cohorts": [
                        {
                            "id": c.get("id"),
                            "name": c.get("name"),
                            "size": c.get("size"),
                            "created": c.get("created"),
                            "last_computed": c.get("last_computed"),
                        }
                        for c in data.get("cohorts", [])
                    ]
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("amplitude_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
