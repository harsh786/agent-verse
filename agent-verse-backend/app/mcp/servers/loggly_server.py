"""Loggly MCP server — search logs and retrieve events via Loggly REST API.

Environment:
  LOGGLY_API_TOKEN:  Loggly API token (for search/retrieval)
  LOGGLY_ACCOUNT:    Loggly subdomain (e.g. mycompany → mycompany.loggly.com)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "loggly_search",
        "description": "Search Loggly log events using a query string",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "default": "*",
                    "description": "Loggly search query (e.g. 'tag:myapp AND severity:ERROR')",
                },
                "from": {
                    "type": "string",
                    "default": "-1h",
                    "description": "Start time (ISO8601, epoch, or relative like -1h)",
                },
                "until": {
                    "type": "string",
                    "default": "now",
                    "description": "End time",
                },
                "size": {"type": "integer", "default": 100},
                "order": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "default": "desc",
                },
            },
        },
    },
    {
        "name": "loggly_get_event",
        "description": "Get a specific Loggly log event by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "loggly_search_iterate",
        "description": "Paginate through Loggly search results using a rsid (result set ID)",
        "parameters": {
            "type": "object",
            "properties": {
                "rsid": {"type": "string", "description": "Result set ID from a previous search"},
                "page": {"type": "integer", "default": 0},
                "size": {"type": "integer", "default": 100},
            },
            "required": ["rsid"],
        },
    },
    {
        "name": "loggly_facets",
        "description": "Get faceted counts for log fields (terms, dates)",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "default": "*"},
                "facet_type": {
                    "type": "string",
                    "enum": ["terms", "dates"],
                    "default": "terms",
                },
                "from": {"type": "string", "default": "-1h"},
                "until": {"type": "string", "default": "now"},
                "size": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "loggly_list_devices",
        "description": "List devices sending logs to Loggly",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "default": 0},
                "page_size": {"type": "integer", "default": 25},
            },
        },
    },
]


def _base_url() -> str:
    account = os.getenv("LOGGLY_ACCOUNT", "")
    if not account:
        return ""
    return f"https://{account}.loggly.com"


def _headers() -> dict[str, str]:
    token = os.getenv("LOGGLY_API_TOKEN", "")
    return {"Authorization": f"bearer {token}"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base_url = _base_url()
    token = os.getenv("LOGGLY_API_TOKEN", "")

    if not base_url:
        return {"error": "LOGGLY_ACCOUNT not configured"}
    if not token:
        return {"error": "LOGGLY_API_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            hdrs = _headers()

            if tool_name == "loggly_search":
                params: dict[str, Any] = {
                    "q": arguments.get("q", "*"),
                    "from": arguments.get("from", "-1h"),
                    "until": arguments.get("until", "now"),
                    "size": arguments.get("size", 100),
                    "order": arguments.get("order", "desc"),
                    "format": "json",
                }
                resp = await client.get(
                    f"{base_url}/apiv2/search",
                    params=params,
                    headers=hdrs,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "total_events": data.get("total_events"),
                    "rsid": data.get("rsid", {}).get("status"),
                    "events": data.get("events", []),
                }

            elif tool_name == "loggly_get_event":
                resp = await client.get(
                    f"{base_url}/apiv2/events/{arguments['event_id']}",
                    headers=hdrs,
                )
                resp.raise_for_status()
                return resp.json()

            elif tool_name == "loggly_search_iterate":
                rsid = arguments["rsid"]
                resp = await client.get(
                    f"{base_url}/apiv2/events",
                    params={
                        "rsid": rsid,
                        "page": arguments.get("page", 0),
                        "size": arguments.get("size", 100),
                        "format": "json",
                    },
                    headers=hdrs,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "events": data.get("events", []),
                    "total_events": data.get("total_events"),
                    "page": arguments.get("page", 0),
                }

            elif tool_name == "loggly_facets":
                facet_type = arguments.get("facet_type", "terms")
                params = {
                    "q": arguments.get("q", "*"),
                    "from": arguments.get("from", "-1h"),
                    "until": arguments.get("until", "now"),
                    "size": arguments.get("size", 10),
                    "format": "json",
                }
                resp = await client.get(
                    f"{base_url}/apiv2/facets/{facet_type}",
                    params=params,
                    headers=hdrs,
                )
                resp.raise_for_status()
                return resp.json()

            elif tool_name == "loggly_list_devices":
                resp = await client.get(
                    f"{base_url}/apiv2/devices",
                    params={
                        "page": arguments.get("page", 0),
                        "page_size": arguments.get("page_size", 25),
                        "format": "json",
                    },
                    headers=hdrs,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "devices": data.get("devices", data),
                    "total": data.get("total"),
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("loggly_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
