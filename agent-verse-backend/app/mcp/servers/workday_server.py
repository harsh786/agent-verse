"""Workday MCP server — workforce and HR data (basic read-only integration).

Environment:
  WORKDAY_CLIENT_ID:     OAuth2 client ID
  WORKDAY_CLIENT_SECRET: OAuth2 client secret
  WORKDAY_TENANT:        Workday tenant ID (e.g. 'mycompany')
  WORKDAY_BASE_URL:      Workday API base URL (e.g. https://wd2-impl-services1.workday.com)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "workday_list_workers",
        "description": "List workers (employees/contractors) from Workday",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
                "include_inactive": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "workday_get_worker",
        "description": "Get a specific worker by their Workday ID",
        "parameters": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string"},
            },
            "required": ["worker_id"],
        },
    },
    {
        "name": "workday_list_organizations",
        "description": "List organizations / departments in Workday",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Organization type filter (e.g. 'Supervisory', 'Cost_Center')",
                },
            },
        },
    },
    {
        "name": "workday_list_job_postings",
        "description": "List open job postings from Workday Recruiting",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "status": {"type": "string", "default": "Open"},
            },
        },
    },
    {
        "name": "workday_get_pay_period",
        "description": "Get pay period information for a worker",
        "parameters": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string"},
                "year": {"type": "integer"},
            },
            "required": ["worker_id"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    client_id = os.getenv("WORKDAY_CLIENT_ID", "")
    client_secret = os.getenv("WORKDAY_CLIENT_SECRET", "")
    tenant = os.getenv("WORKDAY_TENANT", "")
    base_url = os.getenv("WORKDAY_BASE_URL", "").rstrip("/")

    if not all([client_id, client_secret, tenant, base_url]):
        return {
            "error": "WORKDAY_CLIENT_ID, WORKDAY_CLIENT_SECRET, WORKDAY_TENANT, "
            "and WORKDAY_BASE_URL must be configured"
        }

    try:
        # Obtain access token
        async with httpx.AsyncClient(timeout=30.0) as c:
            token_url = f"{base_url}/ccx/oauth2/{tenant}/token"
            token_resp = await c.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            token_resp.raise_for_status()
            access_token = token_resp.json().get("access_token", "")

        api_base = f"{base_url}/ccx/api/v1/{tenant}"

        async with httpx.AsyncClient(headers=_headers(access_token), timeout=30.0) as c:
            if tool_name == "workday_list_workers":
                params: dict[str, Any] = {
                    "limit": arguments.get("limit", 100),
                    "offset": arguments.get("offset", 0),
                }
                if not arguments.get("include_inactive", False):
                    params["includeTerminatedWorkers"] = "false"
                r = await c.get(f"{api_base}/workers", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "workday_get_worker":
                wid = arguments["worker_id"]
                r = await c.get(f"{api_base}/workers/{wid}")
                r.raise_for_status()
                return r.json()

            elif tool_name == "workday_list_organizations":
                params = {}
                if org_type := arguments.get("type"):
                    params["type"] = org_type
                r = await c.get(f"{api_base}/organizations", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "workday_list_job_postings":
                params = {
                    "limit": arguments.get("limit", 50),
                    "status": arguments.get("status", "Open"),
                }
                r = await c.get(f"{api_base}/jobPostings", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "workday_get_pay_period":
                wid = arguments["worker_id"]
                params = {}
                if year := arguments.get("year"):
                    params["year"] = year
                r = await c.get(f"{api_base}/workers/{wid}/payPeriods", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("workday_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
