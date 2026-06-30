"""Greenhouse MCP server — applicant tracking, jobs, candidates, and applications.

Environment:
  GREENHOUSE_API_KEY: Greenhouse Harvest API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://harvest.greenhouse.io/v1"

TOOL_DEFINITIONS = [
    {
        "name": "greenhouse_list_jobs",
        "description": "List all jobs in Greenhouse",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "closed", "draft"],
                    "description": "Filter by job status",
                },
                "department_id": {"type": "integer"},
                "per_page": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "greenhouse_list_candidates",
        "description": "List candidates in Greenhouse",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer", "description": "Filter by job ID"},
                "email": {"type": "string"},
                "per_page": {"type": "integer", "default": 100},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "greenhouse_create_candidate",
        "description": "Create a new candidate in Greenhouse",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "email_addresses": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"value": {"type": "string"}, "type": {"type": "string"}}},
                },
                "phone_numbers": {"type": "array", "items": {"type": "object"}},
                "company": {"type": "string"},
                "title": {"type": "string"},
                "website_addresses": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["first_name", "last_name"],
        },
    },
    {
        "name": "greenhouse_get_candidate",
        "description": "Get a specific candidate by ID",
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "integer"},
            },
            "required": ["candidate_id"],
        },
    },
    {
        "name": "greenhouse_list_applications",
        "description": "List job applications, optionally filtered by candidate or job",
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "integer"},
                "job_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["active", "rejected", "hired"]},
                "per_page": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "greenhouse_advance_application",
        "description": "Advance an application to the next stage",
        "parameters": {
            "type": "object",
            "properties": {
                "application_id": {"type": "integer"},
                "from_stage_id": {"type": "integer", "description": "Current stage ID"},
            },
            "required": ["application_id", "from_stage_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("GREENHOUSE_API_KEY", "")
    if not api_key:
        return {"error": "GREENHOUSE_API_KEY not configured"}

    auth = (api_key, "")

    try:
        async with httpx.AsyncClient(auth=auth, timeout=30.0) as c:
            if tool_name == "greenhouse_list_jobs":
                params: dict[str, Any] = {"per_page": arguments.get("per_page", 100)}
                if status := arguments.get("status"):
                    params["status"] = status
                if did := arguments.get("department_id"):
                    params["department_id"] = did
                r = await c.get(f"{BASE}/jobs", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "greenhouse_list_candidates":
                params = {
                    "per_page": arguments.get("per_page", 100),
                    "page": arguments.get("page", 1),
                }
                if jid := arguments.get("job_id"):
                    params["job_id"] = jid
                if email := arguments.get("email"):
                    params["email"] = email
                r = await c.get(f"{BASE}/candidates", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "greenhouse_create_candidate":
                payload: dict[str, Any] = {
                    "first_name": arguments["first_name"],
                    "last_name": arguments["last_name"],
                }
                for field in ("email_addresses", "phone_numbers", "company", "title", "website_addresses"):
                    if v := arguments.get(field):
                        payload[field] = v
                r = await c.post(f"{BASE}/candidates", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "greenhouse_get_candidate":
                cid = arguments["candidate_id"]
                r = await c.get(f"{BASE}/candidates/{cid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "greenhouse_list_applications":
                params = {"per_page": arguments.get("per_page", 100)}
                if cid := arguments.get("candidate_id"):
                    params["candidate_id"] = cid
                if jid := arguments.get("job_id"):
                    params["job_id"] = jid
                if status := arguments.get("status"):
                    params["status"] = status
                r = await c.get(f"{BASE}/applications", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "greenhouse_advance_application":
                app_id = arguments["application_id"]
                payload = {"from_stage_id": arguments["from_stage_id"]}
                r = await c.post(f"{BASE}/applications/{app_id}/advance", json=payload)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("greenhouse_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
