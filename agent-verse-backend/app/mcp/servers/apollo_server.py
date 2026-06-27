"""Apollo.io MCP server — people & company search/enrichment, email lookup.

Environment variables:
  APOLLO_API_KEY: Apollo.io API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

APOLLO_BASE = "https://api.apollo.io/v1"

TOOL_DEFINITIONS = [
    {
        "name": "apollo_search_people",
        "description": "Search Apollo.io for people matching filters (title, company, location, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "q_keywords": {"type": "string", "description": "Full-text keywords"},
                "person_titles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Job titles to filter by",
                },
                "organization_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Company domains, e.g. ['apollo.io']",
                },
                "person_locations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "apollo_enrich_person",
        "description": "Enrich a person record using email or name + company",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "organization_name": {"type": "string"},
                "domain": {"type": "string"},
            },
        },
    },
    {
        "name": "apollo_search_companies",
        "description": "Search Apollo.io for companies matching filters",
        "parameters": {
            "type": "object",
            "properties": {
                "q_organization_name": {"type": "string"},
                "organization_locations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "organization_num_employees_ranges": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "E.g. ['1,10', '11,50']",
                },
                "page": {"type": "integer", "default": 1},
                "per_page": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "apollo_enrich_company",
        "description": "Enrich a company record by domain",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Company website domain"},
            },
            "required": ["domain"],
        },
    },
    {
        "name": "apollo_get_email",
        "description": "Reveal work email for a person using name and company",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "organization_name": {"type": "string"},
                "domain": {"type": "string"},
                "email": {"type": "string", "description": "Existing email to enrich"},
                "reveal_personal_emails": {"type": "boolean", "default": False},
            },
        },
    },
]


def _headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        "X-Api-Key": os.getenv("APOLLO_API_KEY", ""),
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("APOLLO_API_KEY", "")
    if not api_key:
        return {"error": "APOLLO_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=APOLLO_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "apollo_search_people":
                body: dict[str, Any] = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 10),
                }
                for k in ("q_keywords", "person_titles", "organization_domains", "person_locations"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/mixed_people/search", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "apollo_enrich_person":
                body = {}
                for k in ("email", "first_name", "last_name", "organization_name", "domain"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/people/match", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "apollo_search_companies":
                body = {
                    "page": arguments.get("page", 1),
                    "per_page": arguments.get("per_page", 10),
                }
                for k in ("q_organization_name", "organization_locations", "organization_num_employees_ranges"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/mixed_companies/search", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "apollo_enrich_company":
                r = await c.post(
                    "/organizations/enrich",
                    json={"domain": arguments["domain"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "apollo_get_email":
                body = {}
                for k in ("first_name", "last_name", "organization_name", "domain", "email"):
                    if k in arguments:
                        body[k] = arguments[k]
                if arguments.get("reveal_personal_emails"):
                    body["reveal_personal_emails"] = True
                r = await c.post("/people/match", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("apollo_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
