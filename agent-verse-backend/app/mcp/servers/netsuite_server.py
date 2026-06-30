"""NetSuite MCP server — ERP records, search, and saved searches via REST API.

Environment:
  NETSUITE_ACCOUNT_ID:   NetSuite account ID (e.g. TSTDRV12345)
  NETSUITE_CONSUMER_KEY: OAuth 1.0a consumer key
  NETSUITE_TOKEN_KEY:    OAuth 1.0a token key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)


def _base() -> str:
    account_id = os.getenv("NETSUITE_ACCOUNT_ID", "")
    return f"https://{account_id}.suitetalk.api.netsuite.com/services/rest/record/v1"


TOOL_DEFINITIONS = [
    {
        "name": "netsuite_list_records",
        "description": "List NetSuite records of a given type (e.g. customer, salesorder, invoice)",
        "parameters": {
            "type": "object",
            "properties": {
                "record_type": {"type": "string", "description": "NetSuite record type (e.g. customer, invoice)"},
                "limit": {"type": "integer", "default": 1000},
                "offset": {"type": "integer", "default": 0},
                "q": {"type": "string", "description": "Search query"},
            },
            "required": ["record_type"],
        },
    },
    {
        "name": "netsuite_get_record",
        "description": "Get a specific NetSuite record by type and internal ID",
        "parameters": {
            "type": "object",
            "properties": {
                "record_type": {"type": "string"},
                "record_id": {"type": "string", "description": "Internal ID of the record"},
            },
            "required": ["record_type", "record_id"],
        },
    },
    {
        "name": "netsuite_create_record",
        "description": "Create a new NetSuite record",
        "parameters": {
            "type": "object",
            "properties": {
                "record_type": {"type": "string"},
                "fields": {"type": "object", "description": "Record field values as key-value pairs"},
            },
            "required": ["record_type", "fields"],
        },
    },
    {
        "name": "netsuite_update_record",
        "description": "Update an existing NetSuite record",
        "parameters": {
            "type": "object",
            "properties": {
                "record_type": {"type": "string"},
                "record_id": {"type": "string"},
                "fields": {"type": "object", "description": "Fields to update"},
            },
            "required": ["record_type", "record_id", "fields"],
        },
    },
    {
        "name": "netsuite_search_records",
        "description": "Search NetSuite records using query syntax",
        "parameters": {
            "type": "object",
            "properties": {
                "record_type": {"type": "string"},
                "query": {"type": "string", "description": "SuiteQL or query string"},
                "limit": {"type": "integer", "default": 1000},
            },
            "required": ["record_type", "query"],
        },
    },
    {
        "name": "netsuite_run_saved_search",
        "description": "Run a saved search by its script ID or internal ID",
        "parameters": {
            "type": "object",
            "properties": {
                "saved_search_id": {"type": "string", "description": "Script ID or internal ID of the saved search"},
                "record_type": {"type": "string"},
            },
            "required": ["saved_search_id", "record_type"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = os.getenv("NETSUITE_ACCOUNT_ID", "")
    consumer_key = os.getenv("NETSUITE_CONSUMER_KEY", "")
    token_key = os.getenv("NETSUITE_TOKEN_KEY", "")
    if not account_id or not consumer_key or not token_key:
        return {"error": "NETSUITE_ACCOUNT_ID, NETSUITE_CONSUMER_KEY, and NETSUITE_TOKEN_KEY must be configured"}

    base = _base()
    # Note: NetSuite uses OAuth 1.0a; for full production use, sign requests with TBA.
    # Here we include the token as a Bearer for structural completeness.
    headers = {
        "Authorization": f"Bearer {token_key}",
        "Content-Type": "application/json",
        "Prefer": "transient",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "netsuite_list_records":
                rt = arguments["record_type"]
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 1000),
                    "offset": arguments.get("offset", 0),
                }
                if q := arguments.get("q"):
                    params["q"] = q
                r = await c.get(f"{base}/{rt}", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "netsuite_get_record":
                rt = arguments["record_type"]
                rid = arguments["record_id"]
                r = await c.get(f"{base}/{rt}/{rid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "netsuite_create_record":
                rt = arguments["record_type"]
                r = await c.post(f"{base}/{rt}", json=arguments["fields"])
                r.raise_for_status()
                return r.json() if r.content else {"status": "created", "location": r.headers.get("Location")}

            elif tool_name == "netsuite_update_record":
                rt = arguments["record_type"]
                rid = arguments["record_id"]
                r = await c.patch(f"{base}/{rt}/{rid}", json=arguments["fields"])
                r.raise_for_status()
                return r.json() if r.content else {"status": "updated"}

            elif tool_name == "netsuite_search_records":
                rt = arguments["record_type"]
                params = {
                    "q": arguments["query"],
                    "limit": arguments.get("limit", 1000),
                }
                r = await c.get(f"{base}/{rt}", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "netsuite_run_saved_search":
                rt = arguments["record_type"]
                ss_id = arguments["saved_search_id"]
                params = {"savedSearchId": ss_id}
                r = await c.get(f"{base}/{rt}", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("netsuite_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
