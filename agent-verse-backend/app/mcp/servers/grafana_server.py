"""Grafana MCP server — monitoring and observability.

Environment:
  GRAFANA_URL:     https://your-grafana.example.com
  GRAFANA_API_KEY: Grafana service account token (sa-...)
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

_BASE_URL = os.getenv("GRAFANA_URL", "").rstrip("/")
_API_KEY = os.getenv("GRAFANA_API_KEY", "")

TOOL_DEFINITIONS = [
    {
        "name": "grafana_get_dashboard",
        "description": "Get a Grafana dashboard by UID",
        "parameters": {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Dashboard UID"},
            },
            "required": ["uid"],
        },
    },
    {
        "name": "grafana_list_dashboards",
        "description": "Search and list Grafana dashboards",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "grafana_query_datasource",
        "description": "Query a Grafana datasource (e.g. Prometheus, Loki)",
        "parameters": {
            "type": "object",
            "properties": {
                "datasource_uid": {"type": "string"},
                "query": {"type": "string", "description": "PromQL, LogQL, or SQL query"},
                "from": {"type": "string", "description": "Start time (e.g. 'now-1h')"},
                "to": {"type": "string", "description": "End time (e.g. 'now')"},
            },
            "required": ["datasource_uid", "query"],
        },
    },
    {
        "name": "grafana_create_annotation",
        "description": "Create a Grafana annotation (mark deployment, incident, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Annotation text"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "dashboard_id": {"type": "integer"},
                "time": {"type": "integer", "description": "Unix timestamp in ms"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "grafana_get_alert_rules",
        "description": "List Grafana alert rules",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["firing", "pending", "inactive", "all"],
                    "default": "all",
                },
            },
        },
    },
    {
        "name": "grafana_fire_alert",
        "description": "Trigger a test/manual alert in Grafana",
        "parameters": {
            "type": "object",
            "properties": {
                "rule_uid": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["rule_uid"],
        },
    },
]


async def call_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    if not _BASE_URL:
        return {"error": "GRAFANA_URL not configured"}

    headers = {"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "grafana_get_dashboard":
                resp = await client.get(
                    f"{_BASE_URL}/api/dashboards/uid/{params['uid']}", headers=headers
                )
                resp.raise_for_status()
                return resp.json()

            if tool_name == "grafana_list_dashboards":
                resp = await client.get(
                    f"{_BASE_URL}/api/search",
                    params={
                        "query": params.get("query", ""),
                        "type": "dash-db",
                        "limit": params.get("limit", 20),
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                return {"dashboards": resp.json()}

            if tool_name == "grafana_query_datasource":
                body = {
                    "queries": [
                        {
                            "datasource": {"uid": params["datasource_uid"]},
                            "expr": params["query"],
                            "refId": "A",
                        }
                    ],
                    "from": params.get("from", "now-1h"),
                    "to": params.get("to", "now"),
                }
                resp = await client.post(f"{_BASE_URL}/api/ds/query", json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()

            if tool_name == "grafana_create_annotation":
                import time as _time

                body = {
                    "text": params["text"],
                    "tags": params.get("tags", []),
                    "time": params.get("time", int(_time.time() * 1000)),
                }
                if "dashboard_id" in params:
                    body["dashboardId"] = params["dashboard_id"]
                resp = await client.post(f"{_BASE_URL}/api/annotations", json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()

            if tool_name == "grafana_get_alert_rules":
                state = params.get("state", "all")
                qs = {} if state == "all" else {"state": state}
                resp = await client.get(
                    f"{_BASE_URL}/api/v1/provisioning/alert-rules", params=qs, headers=headers
                )
                resp.raise_for_status()
                return {"alert_rules": resp.json()}

            if tool_name == "grafana_fire_alert":
                # Send a test alert notification
                body = {"message": params.get("message", "Manual alert from AgentVerse")}
                resp = await client.post(
                    f"{_BASE_URL}/api/v1/provisioning/alert-rules/{params['rule_uid']}/test",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                return {"fired": True, "rule_uid": params["rule_uid"]}

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as e:
            logger.warning("grafana.http_error", status=e.response.status_code, tool=tool_name)
            return {"error": f"Grafana API error {e.response.status_code}"}
        except Exception as e:
            logger.error("grafana.tool_error", tool=tool_name, error=str(e))
            return {"error": str(e)}
