"""Splunk MCP server — search events, manage jobs, and query KV store via Splunk REST API.

Environment:
  SPLUNK_URL:      Splunk base URL (e.g. https://splunk.example.com:8089)
  SPLUNK_TOKEN:    Splunk HEC or session token (preferred)
  SPLUNK_USERNAME: Username for basic auth (fallback)
  SPLUNK_PASSWORD: Password for basic auth (fallback)
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "splunk_search",
        "description": "Run a Splunk search query (blocking, max 1000 results)",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "SPL search string (e.g. 'search index=main error | head 100')",
                },
                "earliest_time": {"type": "string", "default": "-1h", "description": "Earliest time (e.g. -1h, -24h, @d)"},
                "latest_time": {"type": "string", "default": "now"},
                "max_count": {"type": "integer", "default": 100},
                "output_mode": {"type": "string", "default": "json"},
            },
            "required": ["search"],
        },
    },
    {
        "name": "splunk_create_search_job",
        "description": "Create an asynchronous Splunk search job and return the job SID",
        "parameters": {
            "type": "object",
            "properties": {
                "search": {"type": "string"},
                "earliest_time": {"type": "string", "default": "-1h"},
                "latest_time": {"type": "string", "default": "now"},
            },
            "required": ["search"],
        },
    },
    {
        "name": "splunk_get_job_results",
        "description": "Get results from a completed Splunk search job",
        "parameters": {
            "type": "object",
            "properties": {
                "sid": {"type": "string", "description": "Search job SID"},
                "count": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
                "output_mode": {"type": "string", "default": "json"},
            },
            "required": ["sid"],
        },
    },
    {
        "name": "splunk_list_indexes",
        "description": "List all Splunk indexes",
        "parameters": {
            "type": "object",
            "properties": {
                "output_mode": {"type": "string", "default": "json"},
            },
        },
    },
    {
        "name": "splunk_get_index_info",
        "description": "Get details about a specific Splunk index",
        "parameters": {
            "type": "object",
            "properties": {
                "index_name": {"type": "string"},
            },
            "required": ["index_name"],
        },
    },
    {
        "name": "splunk_list_saved_searches",
        "description": "List saved searches and reports in Splunk",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {"type": "string", "default": "search"},
                "owner": {"type": "string"},
            },
        },
    },
    {
        "name": "splunk_get_alerts",
        "description": "Get triggered Splunk alerts",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 25},
                "output_mode": {"type": "string", "default": "json"},
            },
        },
    },
]


def _client(base_url: str) -> httpx.AsyncClient:
    headers: dict[str, str] = {}
    auth = None

    if token := os.getenv("SPLUNK_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    elif (user := os.getenv("SPLUNK_USERNAME")) and (pwd := os.getenv("SPLUNK_PASSWORD")):
        auth = (user, pwd)

    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        auth=auth,
        timeout=60.0,
        verify=os.getenv("SPLUNK_VERIFY_SSL", "true").lower() != "false",
    )


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base_url = os.getenv("SPLUNK_URL", "").rstrip("/")
    if not base_url:
        return {"error": "SPLUNK_URL not configured"}

    if not os.getenv("SPLUNK_TOKEN") and not (os.getenv("SPLUNK_USERNAME") and os.getenv("SPLUNK_PASSWORD")):
        return {"error": "SPLUNK_TOKEN or SPLUNK_USERNAME/SPLUNK_PASSWORD not configured"}

    try:
        async with _client(base_url) as client:
            services_base = f"{base_url}/services"

            if tool_name == "splunk_search":
                # Use oneshot search for blocking queries
                resp = await client.post(
                    f"{services_base}/search/jobs/export",
                    data={
                        "search": arguments["search"],
                        "earliest_time": arguments.get("earliest_time", "-1h"),
                        "latest_time": arguments.get("latest_time", "now"),
                        "count": arguments.get("max_count", 100),
                        "output_mode": arguments.get("output_mode", "json"),
                    },
                )
                resp.raise_for_status()
                import json as _json
                events = []
                for line in resp.text.strip().splitlines():
                    try:
                        obj = _json.loads(line)
                        if "result" in obj:
                            events.append(obj["result"])
                    except Exception:
                        pass
                return {"events": events, "count": len(events)}

            elif tool_name == "splunk_create_search_job":
                resp = await client.post(
                    f"{services_base}/search/jobs",
                    data={
                        "search": arguments["search"],
                        "earliest_time": arguments.get("earliest_time", "-1h"),
                        "latest_time": arguments.get("latest_time", "now"),
                        "output_mode": "json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                sid = data.get("sid")
                return {"sid": sid, "status": "created"}

            elif tool_name == "splunk_get_job_results":
                sid = arguments["sid"]
                resp = await client.get(
                    f"{services_base}/search/jobs/{sid}/results",
                    params={
                        "count": arguments.get("count", 100),
                        "offset": arguments.get("offset", 0),
                        "output_mode": arguments.get("output_mode", "json"),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "results": data.get("results", []),
                    "fields": data.get("fields", []),
                }

            elif tool_name == "splunk_list_indexes":
                resp = await client.get(
                    f"{services_base}/data/indexes",
                    params={"output_mode": arguments.get("output_mode", "json"), "count": 0},
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "indexes": [
                        {
                            "name": e.get("name"),
                            "totalEventCount": e.get("content", {}).get("totalEventCount"),
                            "currentDBSizeMB": e.get("content", {}).get("currentDBSizeMB"),
                        }
                        for e in data.get("entry", [])
                    ]
                }

            elif tool_name == "splunk_get_index_info":
                idx = arguments["index_name"]
                resp = await client.get(
                    f"{services_base}/data/indexes/{idx}",
                    params={"output_mode": "json"},
                )
                resp.raise_for_status()
                data = resp.json()
                entries = data.get("entry", [])
                if entries:
                    e = entries[0]
                    return {"name": e.get("name"), "content": e.get("content", {})}
                return {"error": f"Index '{idx}' not found"}

            elif tool_name == "splunk_list_saved_searches":
                params: dict[str, Any] = {
                    "output_mode": "json",
                    "count": 50,
                }
                app = arguments.get("app", "search")
                owner = arguments.get("owner", "nobody")
                resp = await client.get(
                    f"{services_base}/saved/searches",
                    params=params,
                    headers={"X-Splunk-Namespace": f"{app}/{owner}/search"},
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "searches": [
                        {
                            "name": e.get("name"),
                            "search": e.get("content", {}).get("search"),
                            "description": e.get("content", {}).get("description"),
                        }
                        for e in data.get("entry", [])
                    ]
                }

            elif tool_name == "splunk_get_alerts":
                resp = await client.get(
                    f"{services_base}/alerts/fired_alerts",
                    params={
                        "output_mode": arguments.get("output_mode", "json"),
                        "count": arguments.get("count", 25),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "alerts": [
                        {
                            "name": e.get("name"),
                            "triggered_time": e.get("content", {}).get("triggered_time"),
                            "severity": e.get("content", {}).get("severity"),
                        }
                        for e in data.get("entry", [])
                    ]
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("splunk_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
