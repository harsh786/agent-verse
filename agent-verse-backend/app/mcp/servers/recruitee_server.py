"""Recruitee MCP server — applicant tracking, candidates, offers, and stages.

Environment:
  RECRUITEE_API_TOKEN:  Recruitee personal API token
  RECRUITEE_COMPANY_ID: Recruitee company subdomain / ID
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "recruitee_list_offers",
        "description": "List all job offers/positions in Recruitee",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["job", "talent_pool"],
                    "description": "Filter by offer kind",
                },
                "status": {"type": "string", "enum": ["published", "draft", "archived"]},
            },
        },
    },
    {
        "name": "recruitee_list_candidates",
        "description": "List candidates, optionally filtered by offer",
        "parameters": {
            "type": "object",
            "properties": {
                "offer_id": {"type": "integer", "description": "Filter by offer/job ID"},
                "stage_id": {"type": "integer"},
                "limit": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "recruitee_create_candidate",
        "description": "Create a new candidate and optionally apply them to an offer",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "emails": {"type": "array", "items": {"type": "string"}},
                "phones": {"type": "array", "items": {"type": "string"}},
                "offer_id": {"type": "integer", "description": "Apply candidate to this offer"},
                "cv_url": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "recruitee_update_candidate_stage",
        "description": "Move a candidate to a different pipeline stage",
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "integer"},
                "stage_id": {"type": "integer"},
            },
            "required": ["candidate_id", "stage_id"],
        },
    },
    {
        "name": "recruitee_list_stages",
        "description": "List pipeline stages for an offer",
        "parameters": {
            "type": "object",
            "properties": {
                "offer_id": {"type": "integer"},
            },
            "required": ["offer_id"],
        },
    },
    {
        "name": "recruitee_get_statistics",
        "description": "Get recruitment statistics and funnel metrics for the company",
        "parameters": {
            "type": "object",
            "properties": {
                "offer_id": {"type": "integer", "description": "Filter stats for a specific offer"},
                "from_date": {"type": "string", "description": "YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
        },
    },
]


def _base() -> str:
    company_id = os.getenv("RECRUITEE_COMPANY_ID", "")
    return f"https://api.recruitee.com/c/{company_id}"


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_token = os.getenv("RECRUITEE_API_TOKEN", "")
    company_id = os.getenv("RECRUITEE_COMPANY_ID", "")
    if not api_token or not company_id:
        return {"error": "RECRUITEE_API_TOKEN and RECRUITEE_COMPANY_ID must be configured"}

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    base = _base()

    try:
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as c:
            if tool_name == "recruitee_list_offers":
                params: dict[str, Any] = {}
                if kind := arguments.get("kind"):
                    params["kind"] = kind
                if status := arguments.get("status"):
                    params["status"] = status
                r = await c.get(f"{base}/offers", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "recruitee_list_candidates":
                params = {"limit": arguments.get("limit", 100)}
                if oid := arguments.get("offer_id"):
                    params["offer_id"] = oid
                if sid := arguments.get("stage_id"):
                    params["stage_id"] = sid
                r = await c.get(f"{base}/candidates", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "recruitee_create_candidate":
                payload: dict[str, Any] = {
                    "candidate": {
                        "name": arguments["name"],
                    }
                }
                if emails := arguments.get("emails"):
                    payload["candidate"]["emails"] = [{"value": e} for e in emails]
                if phones := arguments.get("phones"):
                    payload["candidate"]["phones"] = [{"value": p} for p in phones]
                if cv_url := arguments.get("cv_url"):
                    payload["candidate"]["cv_url"] = cv_url
                if oid := arguments.get("offer_id"):
                    payload["offer_id"] = oid
                r = await c.post(f"{base}/candidates", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "recruitee_update_candidate_stage":
                cid = arguments["candidate_id"]
                payload = {"stage_id": arguments["stage_id"]}
                r = await c.patch(f"{base}/candidates/{cid}", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "recruitee_list_stages":
                oid = arguments["offer_id"]
                r = await c.get(f"{base}/offers/{oid}/stages")
                r.raise_for_status()
                return r.json()

            elif tool_name == "recruitee_get_statistics":
                params = {}
                if oid := arguments.get("offer_id"):
                    params["offer_id"] = oid
                if fd := arguments.get("from_date"):
                    params["from"] = fd
                if td := arguments.get("to_date"):
                    params["to"] = td
                r = await c.get(f"{base}/stats", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("recruitee_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
