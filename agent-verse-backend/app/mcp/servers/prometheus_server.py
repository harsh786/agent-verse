"""Prometheus MCP server — query metrics and alerts via Prometheus HTTP API.

Environment:
  PROMETHEUS_URL:      Prometheus base URL (e.g. http://prometheus:9090)
  PROMETHEUS_USERNAME: Basic auth username (optional)
  PROMETHEUS_PASSWORD: Basic auth password (optional)
  PROMETHEUS_TOKEN:    Bearer token (optional, takes precedence over basic auth)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "prometheus_query",
        "description": "Execute an instant PromQL query",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL expression"},
                "time": {"type": "string", "description": "Evaluation timestamp (RFC3339 or Unix)"},
                "timeout": {"type": "string", "description": "Evaluation timeout (e.g. 30s)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "prometheus_query_range",
        "description": "Execute a range PromQL query over a time window",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL expression"},
                "start": {"type": "string", "description": "Start time (RFC3339 or Unix)"},
                "end": {"type": "string", "description": "End time (RFC3339 or Unix)"},
                "step": {"type": "string", "description": "Resolution step (e.g. 1m, 5m, 1h)"},
                "timeout": {"type": "string"},
            },
            "required": ["query", "start", "end", "step"],
        },
    },
    {
        "name": "prometheus_list_metrics",
        "description": "List all available metric names in Prometheus",
        "parameters": {
            "type": "object",
            "properties": {
                "match": {"type": "string", "description": "Regex filter on metric names"},
            },
        },
    },
    {
        "name": "prometheus_get_labels",
        "description": "Get all label names or values for a specific label",
        "parameters": {
            "type": "object",
            "properties": {
                "label_name": {"type": "string", "description": "Label name to get values for (omit for all labels)"},
                "match": {"type": "array", "items": {"type": "string"}, "description": "Series selectors"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
        },
    },
    {
        "name": "prometheus_get_alerts",
        "description": "Get currently active Prometheus alerts",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "prometheus_get_targets",
        "description": "Get Prometheus scrape targets and their health status",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["active", "dropped", "any"],
                    "default": "any",
                },
            },
        },
    },
    {
        "name": "prometheus_get_rules",
        "description": "Get Prometheus recording and alerting rules",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["alert", "record"],
                    "description": "Filter by rule type",
                },
            },
        },
    },
]


def _client(base_url: str) -> httpx.AsyncClient:
    headers: dict[str, str] = {}
    auth = None

    if token := os.getenv("PROMETHEUS_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    elif (user := os.getenv("PROMETHEUS_USERNAME")) and (pwd := os.getenv("PROMETHEUS_PASSWORD")):
        auth = (user, pwd)

    return httpx.AsyncClient(base_url=base_url, headers=headers, auth=auth, timeout=30.0)


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base_url = os.getenv("PROMETHEUS_URL", "").rstrip("/")
    if not base_url:
        return {"error": "PROMETHEUS_URL not configured"}

    api_base = f"{base_url}/api/v1"

    try:
        async with _client(base_url) as client:
            if tool_name == "prometheus_query":
                params: dict[str, Any] = {"query": arguments["query"]}
                if t := arguments.get("time"):
                    params["time"] = t
                if timeout := arguments.get("timeout"):
                    params["timeout"] = timeout
                resp = await client.get(f"{api_base}/query", params=params)
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "error":
                    return {"error": data.get("error", "Unknown error"), "errorType": data.get("errorType")}
                result = data.get("data", {})
                return {
                    "resultType": result.get("resultType"),
                    "result": result.get("result", []),
                }

            elif tool_name == "prometheus_query_range":
                params = {
                    "query": arguments["query"],
                    "start": arguments["start"],
                    "end": arguments["end"],
                    "step": arguments["step"],
                }
                if timeout := arguments.get("timeout"):
                    params["timeout"] = timeout
                resp = await client.get(f"{api_base}/query_range", params=params)
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "error":
                    return {"error": data.get("error", "Unknown error")}
                result = data.get("data", {})
                return {
                    "resultType": result.get("resultType"),
                    "result": result.get("result", []),
                }

            elif tool_name == "prometheus_list_metrics":
                params = {}
                if match := arguments.get("match"):
                    params["match[]"] = match
                resp = await client.get(f"{api_base}/label/__name__/values", params=params)
                resp.raise_for_status()
                data = resp.json()
                metrics = data.get("data", [])
                if match_filter := arguments.get("match"):
                    import re
                    try:
                        metrics = [m for m in metrics if re.search(match_filter, m)]
                    except re.error:
                        pass
                return {"metrics": metrics, "count": len(metrics)}

            elif tool_name == "prometheus_get_labels":
                label_name = arguments.get("label_name")
                params = {}
                if matchers := arguments.get("match"):
                    params["match[]"] = matchers
                if start := arguments.get("start"):
                    params["start"] = start
                if end := arguments.get("end"):
                    params["end"] = end

                if label_name:
                    resp = await client.get(f"{api_base}/label/{label_name}/values", params=params)
                else:
                    resp = await client.get(f"{api_base}/labels", params=params)
                resp.raise_for_status()
                data = resp.json()
                return {"labels": data.get("data", [])}

            elif tool_name == "prometheus_get_alerts":
                resp = await client.get(f"{api_base}/alerts")
                resp.raise_for_status()
                data = resp.json()
                alerts = data.get("data", {}).get("alerts", [])
                return {
                    "alerts": [
                        {
                            "name": a.get("labels", {}).get("alertname"),
                            "state": a.get("state"),
                            "severity": a.get("labels", {}).get("severity"),
                            "summary": a.get("annotations", {}).get("summary"),
                            "activeAt": a.get("activeAt"),
                        }
                        for a in alerts
                    ],
                    "count": len(alerts),
                }

            elif tool_name == "prometheus_get_targets":
                params = {}
                if state := arguments.get("state", "any"):
                    if state != "any":
                        params["state"] = state
                resp = await client.get(f"{api_base}/targets", params=params)
                resp.raise_for_status()
                data = resp.json()
                targets_data = data.get("data", {})
                active = targets_data.get("activeTargets", [])
                dropped = targets_data.get("droppedTargets", [])
                return {
                    "activeTargets": [
                        {
                            "scrapeUrl": t.get("scrapeUrl"),
                            "health": t.get("health"),
                            "lastError": t.get("lastError"),
                            "labels": t.get("labels"),
                        }
                        for t in active
                    ],
                    "droppedTargets": len(dropped),
                }

            elif tool_name == "prometheus_get_rules":
                params = {}
                if rule_type := arguments.get("type"):
                    params["type"] = rule_type
                resp = await client.get(f"{api_base}/rules", params=params)
                resp.raise_for_status()
                data = resp.json()
                groups = data.get("data", {}).get("groups", [])
                return {
                    "groups": [
                        {
                            "name": g.get("name"),
                            "file": g.get("file"),
                            "rules": [
                                {
                                    "name": r.get("name"),
                                    "type": r.get("type"),
                                    "health": r.get("health"),
                                    "query": r.get("query"),
                                }
                                for r in g.get("rules", [])
                            ],
                        }
                        for g in groups
                    ]
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("prometheus_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
