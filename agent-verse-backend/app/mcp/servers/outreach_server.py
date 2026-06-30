"""Outreach MCP server — sales engagement: prospects, sequences, and analytics.

Environment variables:
  OUTREACH_ACCESS_TOKEN: Outreach OAuth 2.0 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

OUTREACH_BASE = "https://api.outreach.io/api/v2"

TOOL_DEFINITIONS = [
    {
        "name": "outreach_list_prospects",
        "description": "List prospects in Outreach with optional filtering and pagination",
        "parameters": {
            "type": "object",
            "properties": {
                "filter_email": {"type": "string", "description": "Filter by email address"},
                "filter_name": {"type": "string", "description": "Filter by prospect name"},
                "page_size": {"type": "integer", "default": 25},
                "page_number": {"type": "integer", "default": 1},
                "sort": {"type": "string", "description": "Sort field, e.g. 'createdAt'"},
            },
        },
    },
    {
        "name": "outreach_create_prospect",
        "description": "Create a new prospect in Outreach",
        "parameters": {
            "type": "object",
            "properties": {
                "emails": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Email addresses",
                },
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "title": {"type": "string"},
                "company": {"type": "string"},
                "phones": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "ownerEmail": {"type": "string", "description": "SDR email to own this prospect"},
            },
            "required": ["emails"],
        },
    },
    {
        "name": "outreach_update_prospect",
        "description": "Update an existing prospect in Outreach by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "prospect_id": {"type": "integer", "description": "Outreach prospect ID"},
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "title": {"type": "string"},
                "company": {"type": "string"},
                "emails": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["prospect_id"],
        },
    },
    {
        "name": "outreach_list_sequences",
        "description": "List email/call sequences in Outreach",
        "parameters": {
            "type": "object",
            "properties": {
                "filter_name": {"type": "string", "description": "Filter by sequence name"},
                "page_size": {"type": "integer", "default": 25},
                "page_number": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "outreach_add_to_sequence",
        "description": "Enrol a prospect in an Outreach sequence",
        "parameters": {
            "type": "object",
            "properties": {
                "prospect_id": {"type": "integer", "description": "Prospect ID to enrol"},
                "sequence_id": {"type": "integer", "description": "Sequence ID to enrol the prospect in"},
                "mailbox_id": {"type": "integer", "description": "Mailbox to use for sending"},
            },
            "required": ["prospect_id", "sequence_id"],
        },
    },
    {
        "name": "outreach_get_sequence_stats",
        "description": "Get performance statistics for an Outreach sequence",
        "parameters": {
            "type": "object",
            "properties": {
                "sequence_id": {"type": "integer", "description": "Sequence ID"},
            },
            "required": ["sequence_id"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/vnd.api+json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("OUTREACH_ACCESS_TOKEN", "")
    if not token:
        return {"error": "OUTREACH_ACCESS_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=OUTREACH_BASE, headers=_headers(token), timeout=30.0
        ) as c:
            if tool_name == "outreach_list_prospects":
                params: dict[str, Any] = {
                    "page[size]": arguments.get("page_size", 25),
                    "page[number]": arguments.get("page_number", 1),
                }
                if email := arguments.get("filter_email"):
                    params["filter[emails]"] = email
                if name := arguments.get("filter_name"):
                    params["filter[name]"] = name
                if sort := arguments.get("sort"):
                    params["sort"] = sort
                r = await c.get("/prospects", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "outreach_create_prospect":
                attrs: dict[str, Any] = {}
                if "emails" in arguments:
                    attrs["emails"] = arguments["emails"]
                for k in ("firstName", "lastName", "title", "company", "phones"):
                    if k in arguments:
                        attrs[k] = arguments[k]
                body: dict[str, Any] = {"data": {"type": "prospect", "attributes": attrs}}
                if "ownerEmail" in arguments:
                    body["data"]["relationships"] = {
                        "owner": {"data": {"type": "user", "attributes": {"email": arguments["ownerEmail"]}}}
                    }
                r = await c.post("/prospects", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "outreach_update_prospect":
                pid = arguments["prospect_id"]
                attrs = {}
                for k in ("firstName", "lastName", "title", "company", "emails"):
                    if k in arguments:
                        attrs[k] = arguments[k]
                body = {"data": {"type": "prospect", "id": pid, "attributes": attrs}}
                r = await c.patch(f"/prospects/{pid}", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "outreach_list_sequences":
                params = {
                    "page[size]": arguments.get("page_size", 25),
                    "page[number]": arguments.get("page_number", 1),
                }
                if name := arguments.get("filter_name"):
                    params["filter[name]"] = name
                r = await c.get("/sequences", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "outreach_add_to_sequence":
                body = {
                    "data": {
                        "type": "sequenceState",
                        "relationships": {
                            "prospect": {"data": {"type": "prospect", "id": arguments["prospect_id"]}},
                            "sequence": {"data": {"type": "sequence", "id": arguments["sequence_id"]}},
                        },
                    }
                }
                if "mailbox_id" in arguments:
                    body["data"]["relationships"]["mailbox"] = {
                        "data": {"type": "mailbox", "id": arguments["mailbox_id"]}
                    }
                r = await c.post("/sequenceStates", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "outreach_get_sequence_stats":
                sid = arguments["sequence_id"]
                r = await c.get(f"/sequences/{sid}")
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("outreach_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
