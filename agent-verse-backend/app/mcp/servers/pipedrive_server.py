"""Pipedrive MCP server — deals, persons, organizations, activities, stages.

Environment variables:
  PIPEDRIVE_API_TOKEN: Pipedrive personal API token
  PIPEDRIVE_COMPANY_DOMAIN: Your Pipedrive subdomain (e.g. mycompany)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "pipedrive_list_deals",
        "description": "List Pipedrive deals with optional status filter",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "won", "lost", "deleted", "all_not_deleted"],
                    "default": "all_not_deleted",
                },
                "limit": {"type": "integer", "default": 20},
                "start": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "pipedrive_create_deal",
        "description": "Create a new Pipedrive deal",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "value": {"type": "number", "description": "Deal value"},
                "currency": {"type": "string", "default": "USD"},
                "person_id": {"type": "integer"},
                "org_id": {"type": "integer"},
                "stage_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["open", "won", "lost"]},
            },
            "required": ["title"],
        },
    },
    {
        "name": "pipedrive_update_deal",
        "description": "Update a Pipedrive deal by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "deal_id": {"type": "integer"},
                "title": {"type": "string"},
                "value": {"type": "number"},
                "status": {"type": "string", "enum": ["open", "won", "lost"]},
                "stage_id": {"type": "integer"},
                "fields": {"type": "object", "description": "Additional fields to update"},
            },
            "required": ["deal_id"],
        },
    },
    {
        "name": "pipedrive_list_persons",
        "description": "List Pipedrive persons (contacts)",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "start": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "pipedrive_create_person",
        "description": "Create a new Pipedrive person",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Email addresses [{value: ..., primary: true}]",
                },
                "phone": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "org_id": {"type": "integer"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "pipedrive_list_organizations",
        "description": "List Pipedrive organizations",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "start": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "pipedrive_create_activity",
        "description": "Create an activity (call, email, meeting, task, deadline, lunch)",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["call", "meeting", "task", "deadline", "email", "lunch"],
                    "default": "task",
                },
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "due_time": {"type": "string", "description": "HH:MM"},
                "deal_id": {"type": "integer"},
                "person_id": {"type": "integer"},
                "org_id": {"type": "integer"},
                "note": {"type": "string"},
            },
            "required": ["subject"],
        },
    },
    {
        "name": "pipedrive_get_pipeline_stages",
        "description": "Get all pipeline stages (optionally filter by pipeline)",
        "parameters": {
            "type": "object",
            "properties": {
                "pipeline_id": {"type": "integer", "description": "Filter by pipeline ID"},
            },
        },
    },
]


def _base_url() -> str:
    domain = os.getenv("PIPEDRIVE_COMPANY_DOMAIN", "")
    return f"https://{domain}.pipedrive.com/api/v1" if domain else ""


def _params(extra: dict | None = None) -> dict[str, Any]:
    token = os.getenv("PIPEDRIVE_API_TOKEN", "")
    p: dict[str, Any] = {"api_token": token}
    if extra:
        p.update(extra)
    return p


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base = _base_url()
    token = os.getenv("PIPEDRIVE_API_TOKEN", "")
    if not base or not token:
        return {"error": "PIPEDRIVE_API_TOKEN and PIPEDRIVE_COMPANY_DOMAIN required"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "pipedrive_list_deals":
                r = await c.get(
                    f"{base}/deals",
                    params=_params(
                        {
                            "status": arguments.get("status", "all_not_deleted"),
                            "limit": arguments.get("limit", 20),
                            "start": arguments.get("start", 0),
                        }
                    ),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "pipedrive_create_deal":
                body = {
                    "title": arguments["title"],
                    **{
                        k: arguments[k]
                        for k in ("value", "currency", "person_id", "org_id", "stage_id", "status")
                        if k in arguments
                    },
                }
                r = await c.post(f"{base}/deals", params=_params(), json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pipedrive_update_deal":
                did = arguments["deal_id"]
                body = {
                    k: arguments[k]
                    for k in ("title", "value", "status", "stage_id")
                    if k in arguments
                }
                body.update(arguments.get("fields", {}))
                r = await c.put(f"{base}/deals/{did}", params=_params(), json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pipedrive_list_persons":
                r = await c.get(
                    f"{base}/persons",
                    params=_params(
                        {
                            "limit": arguments.get("limit", 20),
                            "start": arguments.get("start", 0),
                        }
                    ),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "pipedrive_create_person":
                body = {
                    "name": arguments["name"],
                    **{
                        k: arguments[k]
                        for k in ("email", "phone", "org_id")
                        if k in arguments
                    },
                }
                r = await c.post(f"{base}/persons", params=_params(), json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pipedrive_list_organizations":
                r = await c.get(
                    f"{base}/organizations",
                    params=_params(
                        {
                            "limit": arguments.get("limit", 20),
                            "start": arguments.get("start", 0),
                        }
                    ),
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "pipedrive_create_activity":
                body = {
                    "subject": arguments["subject"],
                    "type": arguments.get("type", "task"),
                    **{
                        k: arguments[k]
                        for k in ("due_date", "due_time", "deal_id", "person_id", "org_id", "note")
                        if k in arguments
                    },
                }
                r = await c.post(f"{base}/activities", params=_params(), json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "pipedrive_get_pipeline_stages":
                params = _params()
                if pid := arguments.get("pipeline_id"):
                    params["pipeline_id"] = pid
                r = await c.get(f"{base}/stages", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("pipedrive_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
