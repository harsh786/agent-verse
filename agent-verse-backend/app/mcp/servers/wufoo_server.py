"""Wufoo MCP server — form building, entries, and report data.

Environment:
  WUFOO_API_KEY: Wufoo API key for authentication
  WUFOO_SUBDOMAIN: Wufoo account subdomain (e.g. mycompany)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    subdomain = os.getenv("WUFOO_SUBDOMAIN", "")
    return f"https://{subdomain}.wufoo.com/api/v3"


TOOL_DEFINITIONS = [
    {
        "name": "wufoo_list_forms",
        "description": "List all forms created in the Wufoo account",
        "parameters": {
            "type": "object",
            "properties": {
                "pretty": {"type": "boolean", "description": "Pretty-print JSON response"},
            },
        },
    },
    {
        "name": "wufoo_list_entries",
        "description": "List form submission entries for a specific Wufoo form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_hash": {"type": "string", "description": "Hash ID of the form"},
                "page_start": {"type": "integer", "description": "Pagination start index"},
                "page_size": {"type": "integer", "description": "Entries per page (max 100)"},
                "filter_field": {"type": "string", "description": "Filter by field ID"},
                "filter_operator": {"type": "string", "description": "Filter operator: Is, Is_not, Begins_with"},
                "filter_value": {"type": "string", "description": "Filter value"},
            },
            "required": ["form_hash"],
        },
    },
    {
        "name": "wufoo_submit_entry",
        "description": "Submit a new entry to a Wufoo form programmatically",
        "parameters": {
            "type": "object",
            "properties": {
                "form_hash": {"type": "string", "description": "Hash ID of the form"},
                "fields": {"type": "object", "description": "Form field ID to value mappings (e.g. Field1: 'value')"},
            },
            "required": ["form_hash", "fields"],
        },
    },
    {
        "name": "wufoo_get_field_data",
        "description": "Get the field definitions and structure for a Wufoo form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_hash": {"type": "string", "description": "Hash ID of the form"},
            },
            "required": ["form_hash"],
        },
    },
    {
        "name": "wufoo_list_reports",
        "description": "List all reports created for Wufoo forms",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "wufoo_get_widget_data",
        "description": "Get data for a specific widget within a Wufoo report",
        "parameters": {
            "type": "object",
            "properties": {
                "report_hash": {"type": "string", "description": "Report hash ID"},
                "widget_hash": {"type": "string", "description": "Widget hash ID"},
            },
            "required": ["report_hash", "widget_hash"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("WUFOO_API_KEY", "")
    subdomain = os.getenv("WUFOO_SUBDOMAIN", "")
    if not api_key:
        return {"error": "WUFOO_API_KEY not configured"}
    if not subdomain:
        return {"error": "WUFOO_SUBDOMAIN not configured"}

    base_url = _base_url()
    auth = (api_key, "footastic")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "wufoo_list_forms":
                r = await client.get(f"{base_url}/forms.json", auth=auth)
                r.raise_for_status()
                return r.json()

            if tool_name == "wufoo_list_entries":
                form_hash = arguments["form_hash"]
                params: dict[str, Any] = {}
                if "page_start" in arguments:
                    params["pageStart"] = arguments["page_start"]
                if "page_size" in arguments:
                    params["pageSize"] = arguments["page_size"]
                if "filter_field" in arguments:
                    params["Filter1"] = f"{arguments['filter_field']} {arguments.get('filter_operator', 'Is')} {arguments.get('filter_value', '')}"
                r = await client.get(
                    f"{base_url}/forms/{form_hash}/entries.json",
                    auth=auth,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "wufoo_submit_entry":
                form_hash = arguments["form_hash"]
                r = await client.post(
                    f"{base_url}/forms/{form_hash}/entries.json",
                    auth=auth,
                    data=arguments["fields"],
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "wufoo_get_field_data":
                r = await client.get(
                    f"{base_url}/forms/{arguments['form_hash']}/fields.json",
                    auth=auth,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "wufoo_list_reports":
                r = await client.get(f"{base_url}/reports.json", auth=auth)
                r.raise_for_status()
                return r.json()

            if tool_name == "wufoo_get_widget_data":
                r = await client.get(
                    f"{base_url}/reports/{arguments['report_hash']}/widgets/{arguments['widget_hash']}.json",
                    auth=auth,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
