"""Airtable MCP server — database records management across bases and tables.

Environment variables:
  AIRTABLE_API_KEY: Airtable personal access token
  AIRTABLE_BASE_ID: Default Airtable base ID (can be overridden per call)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

AIRTABLE_BASE = "https://api.airtable.com/v0"

TOOL_DEFINITIONS = [
    {
        "name": "airtable_list_records",
        "description": "List records from an Airtable table with optional filtering, sorting, and field selection",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Table name or ID"},
                "base_id": {"type": "string", "description": "Base ID (overrides AIRTABLE_BASE_ID env var)"},
                "filter_formula": {"type": "string", "description": "Airtable formula for filtering, e.g. \"{Status}='Active'\""},
                "max_records": {"type": "integer", "description": "Max records to return", "default": 100},
                "sort_field": {"type": "string", "description": "Field name to sort by"},
                "sort_direction": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
                "offset": {"type": "string", "description": "Pagination offset token from previous response"},
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "airtable_create_record",
        "description": "Create a new record in an Airtable table",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Table name or ID"},
                "fields": {"type": "object", "description": "Record fields as key-value pairs"},
                "base_id": {"type": "string", "description": "Base ID (overrides AIRTABLE_BASE_ID)"},
            },
            "required": ["table_name", "fields"],
        },
    },
    {
        "name": "airtable_update_record",
        "description": "Update an existing Airtable record by record ID (PATCH — only specified fields changed)",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "record_id": {"type": "string", "description": "Airtable record ID (recXXXXXXXXXXXXXX)"},
                "fields": {"type": "object", "description": "Fields to update"},
                "base_id": {"type": "string"},
            },
            "required": ["table_name", "record_id", "fields"],
        },
    },
    {
        "name": "airtable_delete_record",
        "description": "Permanently delete a record from an Airtable table",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "record_id": {"type": "string", "description": "Airtable record ID to delete"},
                "base_id": {"type": "string"},
            },
            "required": ["table_name", "record_id"],
        },
    },
    {
        "name": "airtable_list_bases",
        "description": "List all Airtable bases accessible with the current API key",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "string", "description": "Pagination offset token"},
            },
        },
    },
    {
        "name": "airtable_search_records",
        "description": "Search records in an Airtable table by matching a value in a specific field",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "search_field": {"type": "string", "description": "Field name to search in"},
                "search_value": {"type": "string", "description": "Value to search for (case-insensitive substring)"},
                "base_id": {"type": "string"},
                "max_records": {"type": "integer", "default": 50},
            },
            "required": ["table_name", "search_field", "search_value"],
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("AIRTABLE_API_KEY", "")
    if not api_key:
        return {"error": "AIRTABLE_API_KEY not configured"}

    default_base_id = os.getenv("AIRTABLE_BASE_ID", "")
    base_id = arguments.get("base_id") or default_base_id

    try:
        async with httpx.AsyncClient(
            headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "airtable_list_records":
                if not base_id:
                    return {"error": "AIRTABLE_BASE_ID not configured and base_id not provided"}
                table = arguments["table_name"]
                params: dict[str, Any] = {"maxRecords": arguments.get("max_records", 100)}
                if "filter_formula" in arguments:
                    params["filterByFormula"] = arguments["filter_formula"]
                if "sort_field" in arguments:
                    params["sort[0][field]"] = arguments["sort_field"]
                    params["sort[0][direction]"] = arguments.get("sort_direction", "asc")
                if "offset" in arguments:
                    params["offset"] = arguments["offset"]
                r = await c.get(f"{AIRTABLE_BASE}/{base_id}/{table}", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "airtable_create_record":
                if not base_id:
                    return {"error": "AIRTABLE_BASE_ID not configured and base_id not provided"}
                table = arguments["table_name"]
                r = await c.post(
                    f"{AIRTABLE_BASE}/{base_id}/{table}",
                    json={"fields": arguments["fields"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "airtable_update_record":
                if not base_id:
                    return {"error": "AIRTABLE_BASE_ID not configured and base_id not provided"}
                table = arguments["table_name"]
                record_id = arguments["record_id"]
                r = await c.patch(
                    f"{AIRTABLE_BASE}/{base_id}/{table}/{record_id}",
                    json={"fields": arguments["fields"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "airtable_delete_record":
                if not base_id:
                    return {"error": "AIRTABLE_BASE_ID not configured and base_id not provided"}
                table = arguments["table_name"]
                record_id = arguments["record_id"]
                r = await c.delete(f"{AIRTABLE_BASE}/{base_id}/{table}/{record_id}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "airtable_list_bases":
                params = {}
                if "offset" in arguments:
                    params["offset"] = arguments["offset"]
                r = await c.get("https://api.airtable.com/v0/meta/bases", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "airtable_search_records":
                if not base_id:
                    return {"error": "AIRTABLE_BASE_ID not configured and base_id not provided"}
                table = arguments["table_name"]
                field = arguments["search_field"]
                value = arguments["search_value"]
                formula = f"SEARCH(LOWER('{value}'), LOWER({{{field}}}))"
                params = {
                    "filterByFormula": formula,
                    "maxRecords": arguments.get("max_records", 50),
                }
                r = await c.get(f"{AIRTABLE_BASE}/{base_id}/{table}", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("airtable_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
