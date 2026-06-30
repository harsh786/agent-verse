"""Gravity Forms MCP server — WordPress form builder data and submissions.

Environment:
  GRAVITY_FORMS_CONSUMER_KEY: Gravity Forms API consumer key
  GRAVITY_FORMS_CONSUMER_SECRET: Gravity Forms API consumer secret
  GRAVITY_FORMS_SITE_URL: WordPress site URL (e.g. https://mysite.com)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base_url() -> str:
    site_url = os.getenv("GRAVITY_FORMS_SITE_URL", "")
    return f"{site_url.rstrip('/')}/wp-json/gf/v2"


TOOL_DEFINITIONS = [
    {
        "name": "gravity_forms_list_forms",
        "description": "List all forms created with Gravity Forms on the WordPress site",
        "parameters": {
            "type": "object",
            "properties": {
                "active": {"type": "boolean", "description": "Filter to only active forms"},
                "page": {"type": "integer", "description": "Page number"},
                "per_page": {"type": "integer", "description": "Forms per page"},
            },
        },
    },
    {
        "name": "gravity_forms_get_entries",
        "description": "Get form submissions (entries) for a specific form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "integer", "description": "Gravity Forms form ID"},
                "search": {"type": "string", "description": "Search query to filter entries"},
                "page_size": {"type": "integer", "description": "Entries per page"},
                "current_page": {"type": "integer", "description": "Page number"},
                "sorting": {"type": "object", "description": "Sorting options"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "gravity_forms_create_entry",
        "description": "Create a new form entry programmatically",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "integer", "description": "Target form ID"},
                "fields": {"type": "object", "description": "Field ID to value mappings for the entry"},
                "ip": {"type": "string", "description": "IP address for the submission"},
            },
            "required": ["form_id", "fields"],
        },
    },
    {
        "name": "gravity_forms_update_entry",
        "description": "Update an existing form entry",
        "parameters": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "integer", "description": "Entry ID to update"},
                "fields": {"type": "object", "description": "Field updates as ID-value pairs"},
            },
            "required": ["entry_id", "fields"],
        },
    },
    {
        "name": "gravity_forms_delete_entry",
        "description": "Delete a specific form entry",
        "parameters": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "integer", "description": "ID of the entry to delete"},
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "gravity_forms_get_form_stats",
        "description": "Get submission statistics and views for a Gravity Form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "integer", "description": "Form ID to get stats for"},
            },
            "required": ["form_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    consumer_key = os.getenv("GRAVITY_FORMS_CONSUMER_KEY", "")
    consumer_secret = os.getenv("GRAVITY_FORMS_CONSUMER_SECRET", "")
    site_url = os.getenv("GRAVITY_FORMS_SITE_URL", "")
    if not consumer_key or not consumer_secret:
        return {"error": "GRAVITY_FORMS_CONSUMER_KEY and GRAVITY_FORMS_CONSUMER_SECRET not configured"}
    if not site_url:
        return {"error": "GRAVITY_FORMS_SITE_URL not configured"}

    base_url = _base_url()
    auth = (consumer_key, consumer_secret)
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "gravity_forms_list_forms":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{base_url}/forms", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "gravity_forms_get_entries":
                form_id = arguments["form_id"]
                params: dict[str, Any] = {}
                if "search" in arguments:
                    params["search"] = arguments["search"]
                if "page_size" in arguments:
                    params["paging[page_size]"] = arguments["page_size"]
                if "current_page" in arguments:
                    params["paging[current_page]"] = arguments["current_page"]
                r = await client.get(f"{base_url}/forms/{form_id}/entries", auth=auth, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "gravity_forms_create_entry":
                payload = {"form_id": arguments["form_id"]}
                payload.update(arguments["fields"])
                if "ip" in arguments:
                    payload["ip"] = arguments["ip"]
                r = await client.post(f"{base_url}/entries", auth=auth, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "gravity_forms_update_entry":
                entry_id = arguments["entry_id"]
                r = await client.put(f"{base_url}/entries/{entry_id}", auth=auth, json=arguments["fields"])
                r.raise_for_status()
                return r.json()

            if tool_name == "gravity_forms_delete_entry":
                r = await client.delete(f"{base_url}/entries/{arguments['entry_id']}", auth=auth)
                r.raise_for_status()
                return {"deleted": True}

            if tool_name == "gravity_forms_get_form_stats":
                r = await client.get(
                    f"{base_url}/forms/{arguments['form_id']}",
                    auth=auth,
                    params={"include": "stats"},
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
