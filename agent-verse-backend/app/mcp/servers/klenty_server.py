"""Klenty MCP server — sales engagement: prospects, cadences, and email analytics.

Environment variables:
  KLENTY_API_KEY: Klenty API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

KLENTY_BASE = "https://app.klenty.com/apis/v1"

TOOL_DEFINITIONS = [
    {
        "name": "klenty_add_prospect",
        "description": "Add a new prospect to Klenty for sales outreach",
        "parameters": {
            "type": "object",
            "properties": {
                "Email": {"type": "string", "description": "Prospect's email address"},
                "FirstName": {"type": "string"},
                "LastName": {"type": "string"},
                "Company": {"type": "string"},
                "Phone": {"type": "string"},
                "JobTitle": {"type": "string"},
                "Website": {"type": "string"},
                "listName": {"type": "string", "description": "Name of the Klenty list to add to"},
            },
            "required": ["Email"],
        },
    },
    {
        "name": "klenty_update_prospect",
        "description": "Update an existing prospect's information in Klenty",
        "parameters": {
            "type": "object",
            "properties": {
                "Email": {"type": "string", "description": "Prospect's email address (identifier)"},
                "FirstName": {"type": "string"},
                "LastName": {"type": "string"},
                "Company": {"type": "string"},
                "Phone": {"type": "string"},
                "JobTitle": {"type": "string"},
            },
            "required": ["Email"],
        },
    },
    {
        "name": "klenty_list_prospects",
        "description": "List prospects in Klenty with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 1},
                "pageLength": {"type": "integer", "description": "Records per page", "default": 25},
                "listName": {"type": "string", "description": "Filter by list name"},
            },
        },
    },
    {
        "name": "klenty_start_cadence",
        "description": "Enrol a prospect into a Klenty cadence (email sequence)",
        "parameters": {
            "type": "object",
            "properties": {
                "Email": {"type": "string", "description": "Prospect's email address"},
                "CadenceName": {"type": "string", "description": "Name of the cadence to start"},
                "listName": {"type": "string", "description": "List name (if required by cadence)"},
            },
            "required": ["Email", "CadenceName"],
        },
    },
    {
        "name": "klenty_pause_cadence",
        "description": "Pause a prospect's active cadence in Klenty",
        "parameters": {
            "type": "object",
            "properties": {
                "Email": {"type": "string", "description": "Prospect's email address"},
                "CadenceName": {"type": "string", "description": "Name of the cadence to pause"},
            },
            "required": ["Email", "CadenceName"],
        },
    },
    {
        "name": "klenty_get_email_stats",
        "description": "Get email performance statistics for a Klenty cadence",
        "parameters": {
            "type": "object",
            "properties": {
                "CadenceName": {"type": "string", "description": "Cadence name to get stats for"},
                "from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            },
            "required": ["CadenceName"],
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "x-API-KEY": api_key,
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("KLENTY_API_KEY", "")
    if not api_key:
        return {"error": "KLENTY_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=KLENTY_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "klenty_add_prospect":
                body: dict[str, Any] = {"Email": arguments["Email"]}
                for k in ("FirstName", "LastName", "Company", "Phone", "JobTitle", "Website", "listName"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/prospect/add", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "klenty_update_prospect":
                body = {"Email": arguments["Email"]}
                for k in ("FirstName", "LastName", "Company", "Phone", "JobTitle"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/prospect/update", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "klenty_list_prospects":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "pageLength": arguments.get("pageLength", 25),
                }
                if "listName" in arguments:
                    params["listName"] = arguments["listName"]
                r = await c.get("/prospect/list", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "klenty_start_cadence":
                body = {
                    "Email": arguments["Email"],
                    "CadenceName": arguments["CadenceName"],
                }
                if "listName" in arguments:
                    body["listName"] = arguments["listName"]
                r = await c.post("/prospect/startcadence", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "klenty_pause_cadence":
                body = {
                    "Email": arguments["Email"],
                    "CadenceName": arguments["CadenceName"],
                }
                r = await c.post("/prospect/pausecadence", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "klenty_get_email_stats":
                params = {"CadenceName": arguments["CadenceName"]}
                if "from" in arguments:
                    params["from"] = arguments["from"]
                if "to" in arguments:
                    params["to"] = arguments["to"]
                r = await c.get("/cadence/stats", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("klenty_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
