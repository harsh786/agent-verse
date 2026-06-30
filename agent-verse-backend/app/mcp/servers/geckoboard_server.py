"""Geckoboard MCP server — business dashboards and dataset management.

Environment:
  GECKOBOARD_API_KEY: Geckoboard API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.geckoboard.com"

TOOL_DEFINITIONS = [
    {
        "name": "geckoboard_list_dashboards",
        "description": "List all dashboards in the Geckoboard account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number for pagination"},
            },
        },
    },
    {
        "name": "geckoboard_push_data",
        "description": "Push data to a Geckoboard dataset to update dashboard widgets",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string", "description": "Unique identifier of the dataset"},
                "data": {
                    "type": "array",
                    "description": "Array of data objects matching the dataset schema",
                },
                "delete_by": {
                    "type": "array",
                    "description": "Field names to use for upsert/delete logic",
                    "items": {"type": "string"},
                },
            },
            "required": ["dataset_id", "data"],
        },
    },
    {
        "name": "geckoboard_list_datasets",
        "description": "List all datasets defined in the Geckoboard account",
        "parameters": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "geckoboard_create_dataset",
        "description": "Create a new dataset with a defined schema in Geckoboard",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string", "description": "Unique ID for the new dataset"},
                "fields": {
                    "type": "object",
                    "description": "Field definitions as key->type mappings (e.g. {revenue: {type: number}})",
                },
                "unique_by": {
                    "type": "array",
                    "description": "List of field names that uniquely identify a record",
                    "items": {"type": "string"},
                },
            },
            "required": ["dataset_id", "fields"],
        },
    },
    {
        "name": "geckoboard_update_dataset",
        "description": "Replace all data in a Geckoboard dataset with new records",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string", "description": "ID of the dataset to update"},
                "data": {
                    "type": "array",
                    "description": "New array of data objects to replace existing data",
                },
            },
            "required": ["dataset_id", "data"],
        },
    },
    {
        "name": "geckoboard_delete_dataset_item",
        "description": "Delete a Geckoboard dataset or remove specific items from it",
        "parameters": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string", "description": "ID of the dataset"},
            },
            "required": ["dataset_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("GECKOBOARD_API_KEY", "")
    if not api_key:
        return {"error": "GECKOBOARD_API_KEY not configured"}

    auth = (api_key, "")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "geckoboard_list_dashboards":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/dashboards", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "geckoboard_push_data":
                dataset_id = arguments["dataset_id"]
                payload: dict[str, Any] = {"data": arguments["data"]}
                if "delete_by" in arguments:
                    payload["delete_by"] = arguments["delete_by"]
                r = await client.post(
                    f"{BASE_URL}/datasets/{dataset_id}/data",
                    auth=auth,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "geckoboard_list_datasets":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/datasets", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "geckoboard_create_dataset":
                dataset_id = arguments["dataset_id"]
                payload = {"fields": arguments["fields"]}
                if "unique_by" in arguments:
                    payload["unique_by"] = arguments["unique_by"]
                r = await client.put(
                    f"{BASE_URL}/datasets/{dataset_id}",
                    auth=auth,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "geckoboard_update_dataset":
                dataset_id = arguments["dataset_id"]
                r = await client.put(
                    f"{BASE_URL}/datasets/{dataset_id}/data",
                    auth=auth,
                    json={"data": arguments["data"]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "geckoboard_delete_dataset_item":
                dataset_id = arguments["dataset_id"]
                r = await client.delete(
                    f"{BASE_URL}/datasets/{dataset_id}",
                    auth=auth,
                )
                r.raise_for_status()
                return {"deleted": True, "dataset_id": dataset_id}

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
