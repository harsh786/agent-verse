"""Upwork MCP server — freelance jobs, proposals, contracts, and profiles.

Environment:
  UPWORK_ACCESS_TOKEN: Upwork OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://www.upwork.com/api"

TOOL_DEFINITIONS = [
    {
        "name": "upwork_search_jobs",
        "description": "Search for freelance jobs posted on Upwork",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Job search query"},
                "category2": {"type": "string", "description": "Job category filter"},
                "skills": {"type": "string", "description": "Required skills (comma-separated)"},
                "budget_from": {"type": "number", "description": "Minimum budget"},
                "budget_to": {"type": "number", "description": "Maximum budget"},
                "page": {"type": "integer", "description": "Page number"},
                "paging": {"type": "integer", "description": "Results per page"},
            },
            "required": ["q"],
        },
    },
    {
        "name": "upwork_get_job",
        "description": "Get detailed information about a specific Upwork job posting",
        "parameters": {
            "type": "object",
            "properties": {
                "job_key": {"type": "string", "description": "Upwork job key (ID)"},
            },
            "required": ["job_key"],
        },
    },
    {
        "name": "upwork_list_proposals",
        "description": "List proposals submitted by the authenticated freelancer",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: active, archived, declined"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "upwork_create_proposal",
        "description": "Submit a proposal (bid) for a job on Upwork",
        "parameters": {
            "type": "object",
            "properties": {
                "job_key": {"type": "string", "description": "Job key to submit proposal for"},
                "cover_letter": {"type": "string", "description": "Cover letter text"},
                "charge_rate": {"type": "number", "description": "Hourly rate or fixed price bid"},
                "milestone_schedule": {"type": "array", "description": "Milestone schedule for fixed-price jobs", "items": {"type": "object"}},
            },
            "required": ["job_key", "cover_letter"],
        },
    },
    {
        "name": "upwork_list_contracts",
        "description": "List active and past contracts for the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: active, closed"},
                "page": {"type": "integer", "description": "Page number"},
            },
        },
    },
    {
        "name": "upwork_get_profile",
        "description": "Get the authenticated freelancer's profile information",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Upwork username (default: current user)"},
            },
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("UPWORK_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "UPWORK_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "upwork_search_jobs":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(
                    f"{BASE_URL}/profiles/v2/search/jobs.json",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "upwork_get_job":
                r = await client.get(
                    f"{BASE_URL}/profiles/v1/jobs/{arguments['job_key']}.json",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "upwork_list_proposals":
                params: dict[str, Any] = {}
                if "status" in arguments:
                    params["status"] = arguments["status"]
                if "page" in arguments:
                    params["page"] = arguments["page"]
                r = await client.get(
                    f"{BASE_URL}/hr/v4/freelancers/me/applications.json",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "upwork_create_proposal":
                r = await client.post(
                    f"{BASE_URL}/hr/v4/freelancers/me/jobs/{arguments['job_key']}/applications.json",
                    headers=headers,
                    json={
                        "cover_letter": arguments["cover_letter"],
                        "charge_rate": arguments.get("charge_rate", 0),
                        "milestone_schedule": arguments.get("milestone_schedule", []),
                    },
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "upwork_list_contracts":
                params = {}
                if "status" in arguments:
                    params["status"] = arguments["status"]
                if "page" in arguments:
                    params["page"] = arguments["page"]
                r = await client.get(
                    f"{BASE_URL}/hr/v2/contracts.json",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "upwork_get_profile":
                username = arguments.get("username", "~")
                r = await client.get(
                    f"{BASE_URL}/profiles/v1/contractors/{username}.json",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
