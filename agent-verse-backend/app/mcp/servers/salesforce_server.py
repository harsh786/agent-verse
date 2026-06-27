"""Salesforce MCP server — SOQL queries, records CRUD, metadata, SOSL search.

Environment variables:
  SALESFORCE_INSTANCE_URL: e.g. https://yourorg.my.salesforce.com
  SALESFORCE_ACCESS_TOKEN: OAuth2 bearer token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

API_VERSION = "v59.0"

TOOL_DEFINITIONS = [
    {
        "name": "salesforce_query",
        "description": "Execute a SOQL query and return matching records",
        "parameters": {
            "type": "object",
            "properties": {
                "soql": {
                    "type": "string",
                    "description": "SOQL query, e.g. SELECT Id, Name FROM Account LIMIT 10",
                },
            },
            "required": ["soql"],
        },
    },
    {
        "name": "salesforce_create_record",
        "description": "Create a new record in a Salesforce object (Account, Contact, Lead, Opportunity, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string", "description": "Salesforce object API name"},
                "fields": {"type": "object", "description": "Field name-value pairs for the new record"},
            },
            "required": ["object_type", "fields"],
        },
    },
    {
        "name": "salesforce_update_record",
        "description": "Update an existing Salesforce record by Id",
        "parameters": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string"},
                "record_id": {"type": "string", "description": "Salesforce record Id (15 or 18 chars)"},
                "fields": {"type": "object", "description": "Fields to update"},
            },
            "required": ["object_type", "record_id", "fields"],
        },
    },
    {
        "name": "salesforce_delete_record",
        "description": "Delete a Salesforce record by Id",
        "parameters": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string"},
                "record_id": {"type": "string"},
            },
            "required": ["object_type", "record_id"],
        },
    },
    {
        "name": "salesforce_describe_object",
        "description": "Get field metadata (name, type, label) for a Salesforce object",
        "parameters": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string"},
            },
            "required": ["object_type"],
        },
    },
    {
        "name": "salesforce_search",
        "description": "Full-text search across Salesforce objects using SOSL",
        "parameters": {
            "type": "object",
            "properties": {
                "sosl": {
                    "type": "string",
                    "description": "SOSL query, e.g. FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name)",
                },
            },
            "required": ["sosl"],
        },
    },
    {
        "name": "salesforce_get_record",
        "description": "Get a single Salesforce record by Id",
        "parameters": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string"},
                "record_id": {"type": "string"},
                "fields": {
                    "type": "string",
                    "description": "Comma-separated field names; omit for default fields",
                    "default": "",
                },
            },
            "required": ["object_type", "record_id"],
        },
    },
]


def _auth_headers() -> dict[str, str]:
    token = os.getenv("SALESFORCE_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base = os.getenv("SALESFORCE_INSTANCE_URL", "").rstrip("/")
    if not base or not os.getenv("SALESFORCE_ACCESS_TOKEN"):
        return {"error": "SALESFORCE_INSTANCE_URL and SALESFORCE_ACCESS_TOKEN required"}

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=_auth_headers()) as c:
            if tool_name == "salesforce_query":
                soql = arguments["soql"]
                r = await c.get(
                    f"{base}/services/data/{API_VERSION}/query",
                    params={"q": soql},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "salesforce_create_record":
                obj = arguments["object_type"]
                r = await c.post(
                    f"{base}/services/data/{API_VERSION}/sobjects/{obj}/",
                    json=arguments.get("fields", {}),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "salesforce_update_record":
                obj, rid = arguments["object_type"], arguments["record_id"]
                r = await c.patch(
                    f"{base}/services/data/{API_VERSION}/sobjects/{obj}/{rid}",
                    json=arguments.get("fields", {}),
                )
                return {"success": r.status_code == 204, "status_code": r.status_code}

            elif tool_name == "salesforce_delete_record":
                obj, rid = arguments["object_type"], arguments["record_id"]
                r = await c.delete(
                    f"{base}/services/data/{API_VERSION}/sobjects/{obj}/{rid}"
                )
                return {"success": r.status_code == 204}

            elif tool_name == "salesforce_describe_object":
                obj = arguments["object_type"]
                r = await c.get(
                    f"{base}/services/data/{API_VERSION}/sobjects/{obj}/describe/"
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "name": data.get("name"),
                    "label": data.get("label"),
                    "fields": [
                        {"name": f["name"], "type": f["type"], "label": f["label"]}
                        for f in data.get("fields", [])[:50]
                    ],
                }

            elif tool_name == "salesforce_search":
                sosl = arguments["sosl"]
                r = await c.get(
                    f"{base}/services/data/{API_VERSION}/search",
                    params={"q": sosl},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "salesforce_get_record":
                obj, rid = arguments["object_type"], arguments["record_id"]
                fields = arguments.get("fields", "")
                url = f"{base}/services/data/{API_VERSION}/sobjects/{obj}/{rid}"
                params = {"fields": fields} if fields else {}
                r = await c.get(url, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("salesforce_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
