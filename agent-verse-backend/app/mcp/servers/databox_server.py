"""Databox MCP server — business analytics dashboards and KPI tracking.

Environment:
  DATABOX_API_KEY: Databox API key for authentication (used as HTTP Basic password)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://push.databox.com"

TOOL_DEFINITIONS = [
    {
        "name": "databox_push_data",
        "description": "Push metric data to a Databox Push Token (databoard datasource)",
        "parameters": {
            "type": "object",
            "properties": {
                "push_token": {"type": "string", "description": "Databox Push Token for the datasource"},
                "data": {
                    "type": "array",
                    "description": "Array of metric objects with 'key' and 'value' fields",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "number"},
                            "date": {"type": "string", "description": "ISO 8601 date string"},
                        },
                    },
                },
            },
            "required": ["push_token", "data"],
        },
    },
    {
        "name": "databox_get_metrics",
        "description": "Retrieve metric names and their last pushed values for a Push Token",
        "parameters": {
            "type": "object",
            "properties": {
                "push_token": {"type": "string", "description": "Databox Push Token"},
            },
            "required": ["push_token"],
        },
    },
    {
        "name": "databox_list_databoards",
        "description": "List all Databox databoards in the account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number for pagination"},
                "per_page": {"type": "integer", "description": "Results per page (max 50)"},
            },
        },
    },
    {
        "name": "databox_list_datablocks",
        "description": "List all datablocks (widgets) available in the account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number for pagination"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "databox_get_performance",
        "description": "Get performance data for a specific databoard by its ID",
        "parameters": {
            "type": "object",
            "properties": {
                "databoard_id": {"type": "string", "description": "ID of the databoard"},
                "date_from": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "date_to": {"type": "string", "description": "End date in YYYY-MM-DD format"},
            },
            "required": ["databoard_id"],
        },
    },
    {
        "name": "databox_create_datablock",
        "description": "Create a new datablock (metric widget) in a databoard",
        "parameters": {
            "type": "object",
            "properties": {
                "databoard_id": {"type": "string", "description": "ID of the target databoard"},
                "name": {"type": "string", "description": "Name of the datablock"},
                "metric_key": {"type": "string", "description": "Metric key to track"},
                "visualization": {"type": "string", "description": "Visualization type (e.g. number, line, bar)"},
            },
            "required": ["databoard_id", "name", "metric_key"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("DATABOX_API_KEY", "")
    if not api_key:
        return {"error": "DATABOX_API_KEY not configured"}

    auth = (api_key, "")  # Databox uses API key as Basic Auth username
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "databox_push_data":
                push_token = arguments["push_token"]
                r = await client.post(
                    f"{BASE_URL}/",
                    auth=(push_token, ""),
                    json={"data": arguments["data"]},
                    headers={"Accept": "application/vnd.databox.v2+json"},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "databox_get_metrics":
                push_token = arguments["push_token"]
                r = await client.get(
                    f"{BASE_URL}/lastpushes",
                    auth=(push_token, ""),
                    headers={"Accept": "application/vnd.databox.v2+json"},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "databox_list_databoards":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(
                    f"{BASE_URL}/databoards",
                    auth=auth,
                    params=params,
                    headers={"Accept": "application/vnd.databox.v2+json"},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "databox_list_datablocks":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(
                    f"{BASE_URL}/datablocks",
                    auth=auth,
                    params=params,
                    headers={"Accept": "application/vnd.databox.v2+json"},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "databox_get_performance":
                databoard_id = arguments["databoard_id"]
                params = {k: v for k, v in arguments.items() if k != "databoard_id" and v is not None}
                r = await client.get(
                    f"{BASE_URL}/databoards/{databoard_id}/performance",
                    auth=auth,
                    params=params,
                    headers={"Accept": "application/vnd.databox.v2+json"},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "databox_create_datablock":
                databoard_id = arguments.pop("databoard_id")
                r = await client.post(
                    f"{BASE_URL}/databoards/{databoard_id}/datablocks",
                    auth=auth,
                    json={k: v for k, v in arguments.items() if v is not None},
                    headers={"Accept": "application/vnd.databox.v2+json"},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
