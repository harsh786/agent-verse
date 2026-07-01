"""Datadog MCP server — monitors, metrics, dashboards, hosts, events, and logs.

Environment variables:
  DATADOG_API_KEY: Datadog API key (DD-API-KEY header)
  DATADOG_APP_KEY: Datadog Application key (DD-APPLICATION-KEY header)
  DATADOG_SITE: Datadog site (default: datadoghq.com; EU: datadoghq.eu)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

_SITE = os.getenv("DATADOG_SITE", "datadoghq.com")
DATADOG_BASE_URL = f"https://api.{_SITE}"

TOOL_DEFINITIONS = [
    {
        "name": "datadog_list_monitors",
        "description": "List all Datadog monitors. Optionally filter by name or tags.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Filter monitors by name substring"},
                "tags": {
                    "type": "string",
                    "description": "Comma-separated tag filters, e.g. 'env:prod,team:backend'",
                },
                "page": {"type": "integer", "default": 0},
                "page_size": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "datadog_get_monitor",
        "description": "Get a single Datadog monitor by its numeric ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "monitor_id": {"type": "integer", "description": "Numeric monitor ID"},
            },
            "required": ["monitor_id"],
        },
    },
    {
        "name": "datadog_create_monitor",
        "description": "Create a new Datadog alert monitor.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Monitor name"},
                "type": {
                    "type": "string",
                    "description": "Monitor type, e.g. 'metric alert', 'log alert', 'service check'",
                },
                "query": {"type": "string", "description": "Monitor query string"},
                "message": {"type": "string", "description": "Notification message"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to apply, e.g. ['env:prod']",
                },
                "priority": {
                    "type": "integer",
                    "description": "Monitor priority 1–5 (1=critical)",
                },
            },
            "required": ["name", "type", "query"],
        },
    },
    {
        "name": "datadog_query_metrics",
        "description": "Query Datadog time-series metrics for a given time window.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Datadog metrics query, e.g. 'avg:system.cpu.user{*}'",
                },
                "from_ts": {
                    "type": "integer",
                    "description": "Start of query window as Unix epoch seconds",
                },
                "to_ts": {
                    "type": "integer",
                    "description": "End of query window as Unix epoch seconds",
                },
            },
            "required": ["query", "from_ts", "to_ts"],
        },
    },
    {
        "name": "datadog_list_dashboards",
        "description": "List all Datadog dashboards accessible to the current API credentials.",
        "parameters": {
            "type": "object",
            "properties": {
                "filter_shared": {
                    "type": "boolean",
                    "description": "If true, only return shared dashboards",
                },
            },
        },
    },
    {
        "name": "datadog_list_hosts",
        "description": "List active hosts reporting to Datadog.",
        "parameters": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Filter string, e.g. 'env:prod'",
                },
                "count": {"type": "integer", "default": 100},
                "start": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "datadog_send_event",
        "description": "Post a custom event to the Datadog event stream.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "text": {"type": "string", "description": "Event body text"},
                "alert_type": {
                    "type": "string",
                    "enum": ["error", "warning", "info", "success"],
                    "default": "info",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to attach, e.g. ['env:prod']",
                },
                "host": {"type": "string", "description": "Host to associate with the event"},
            },
            "required": ["title", "text"],
        },
    },
    {
        "name": "datadog_list_logs",
        "description": "Search Datadog logs using a query string and time range.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Log search query, e.g. 'service:web status:error'",
                },
                "from_ts": {
                    "type": "string",
                    "description": "ISO 8601 start time, e.g. '2024-01-01T00:00:00Z'",
                },
                "to_ts": {
                    "type": "string",
                    "description": "ISO 8601 end time, e.g. '2024-01-02T00:00:00Z'",
                },
                "limit": {"type": "integer", "default": 50},
                "sort": {
                    "type": "string",
                    "enum": ["timestamp", "-timestamp"],
                    "default": "-timestamp",
                    "description": "Sort order: '-timestamp' = newest first",
                },
            },
            "required": ["query", "from_ts", "to_ts"],
        },
    },
]


def _headers() -> dict[str, str]:
    api_key = os.getenv("DATADOG_API_KEY", "")
    app_key = os.getenv("DATADOG_APP_KEY", "")
    return {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    api_key = os.getenv("DATADOG_API_KEY", "")
    if not api_key:
        return {"error": "DATADOG_API_KEY not configured"}

    site = os.getenv("DATADOG_SITE", "datadoghq.com")
    base_url = f"https://api.{site}"

    try:
        async with httpx.AsyncClient(
            base_url=base_url, headers=_headers(), timeout=30.0
        ) as client:
            if tool_name == "datadog_list_monitors":
                params: dict[str, Any] = {
                    "page": arguments.get("page", 0),
                    "page_size": arguments.get("page_size", 100),
                }
                if arguments.get("name"):
                    params["name"] = arguments["name"]
                if arguments.get("tags"):
                    params["tags"] = arguments["tags"]
                resp = await client.get("/api/v1/monitor", params=params)
                resp.raise_for_status()
                monitors = resp.json()
                return {
                    "count": len(monitors),
                    "monitors": [
                        {
                            "id": m["id"],
                            "name": m["name"],
                            "type": m["type"],
                            "status": m.get("overall_state", ""),
                            "tags": m.get("tags", []),
                        }
                        for m in monitors
                    ],
                }

            elif tool_name == "datadog_get_monitor":
                resp = await client.get(f"/api/v1/monitor/{arguments['monitor_id']}")
                resp.raise_for_status()
                m = resp.json()
                return {
                    "id": m["id"],
                    "name": m["name"],
                    "type": m["type"],
                    "query": m.get("query", ""),
                    "message": m.get("message", ""),
                    "status": m.get("overall_state", ""),
                    "tags": m.get("tags", []),
                    "created": m.get("created", ""),
                    "modified": m.get("modified", ""),
                }

            elif tool_name == "datadog_create_monitor":
                payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "type": arguments["type"],
                    "query": arguments["query"],
                    "message": arguments.get("message", ""),
                }
                if arguments.get("tags"):
                    payload["tags"] = arguments["tags"]
                if arguments.get("priority"):
                    payload["priority"] = arguments["priority"]
                resp = await client.post("/api/v1/monitor", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return {"id": data["id"], "name": data["name"], "type": data["type"]}

            elif tool_name == "datadog_query_metrics":
                resp = await client.get(
                    "/api/v1/query",
                    params={
                        "from": arguments["from_ts"],
                        "to": arguments["to_ts"],
                        "query": arguments["query"],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "status": data.get("status", ""),
                    "query": data.get("query", ""),
                    "series": [
                        {
                            "metric": s.get("metric", ""),
                            "display_name": s.get("display_name", ""),
                            "pointlist": s.get("pointlist", []),
                        }
                        for s in data.get("series", [])
                    ],
                }

            elif tool_name == "datadog_list_dashboards":
                params = {}
                if arguments.get("filter_shared"):
                    params["filter[shared]"] = "true"
                resp = await client.get("/api/v1/dashboard", params=params)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "dashboards": [
                        {
                            "id": d["id"],
                            "title": d["title"],
                            "url": d.get("url", ""),
                            "author": (d.get("author_handle") or ""),
                            "modified_at": d.get("modified_at", ""),
                        }
                        for d in data.get("dashboards", [])
                    ]
                }

            elif tool_name == "datadog_list_hosts":
                params = {
                    "count": arguments.get("count", 100),
                    "start": arguments.get("start", 0),
                }
                if arguments.get("filter"):
                    params["filter"] = arguments["filter"]
                resp = await client.get("/api/v1/hosts", params=params)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "total_matching": data.get("total_matching", 0),
                    "hosts": [
                        {
                            "id": h.get("id"),
                            "name": h.get("name", ""),
                            "aliases": h.get("aliases", []),
                            "tags_by_source": h.get("tags_by_source", {}),
                            "up": h.get("up", False),
                        }
                        for h in data.get("host_list", [])
                    ],
                }

            elif tool_name == "datadog_send_event":
                payload = {
                    "title": arguments["title"],
                    "text": arguments["text"],
                    "alert_type": arguments.get("alert_type", "info"),
                }
                if arguments.get("tags"):
                    payload["tags"] = arguments["tags"]
                if arguments.get("host"):
                    payload["host"] = arguments["host"]
                resp = await client.post("/api/v1/events", json=payload)
                resp.raise_for_status()
                data = resp.json()
                event = data.get("event", {})
                return {
                    "id": event.get("id"),
                    "title": event.get("title", ""),
                    "url": event.get("url", ""),
                    "status": data.get("status", ""),
                }

            elif tool_name == "datadog_list_logs":
                payload = {
                    "filter": {
                        "query": arguments["query"],
                        "from": arguments["from_ts"],
                        "to": arguments["to_ts"],
                    },
                    "sort": arguments.get("sort", "-timestamp"),
                    "page": {"limit": arguments.get("limit", 50)},
                }
                resp = await client.post("/api/v2/logs/events/search", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return {
                    "count": len(data.get("data", [])),
                    "logs": [
                        {
                            "id": log.get("id"),
                            "timestamp": (log.get("attributes") or {}).get("timestamp", ""),
                            "message": (log.get("attributes") or {}).get("message", ""),
                            "service": (log.get("attributes") or {}).get("service", ""),
                            "status": (log.get("attributes") or {}).get("status", ""),
                        }
                        for log in data.get("data", [])
                    ],
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        error_body = ""
        try:
            error_body = exc.response.text[:500]
        except Exception:
            pass
        return {
            "error": f"HTTP {exc.response.status_code}: {error_body}",
            "status_code": exc.response.status_code,
        }
    except Exception as exc:
        logger.error("call_tool_failed tool=%s error=%s", tool_name, str(exc))
        return {"error": str(exc)}
