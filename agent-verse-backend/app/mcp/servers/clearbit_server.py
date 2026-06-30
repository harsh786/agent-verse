"""Clearbit MCP server — person/company enrichment, email lookup, and IP reveal.

Environment variables:
  CLEARBIT_API_KEY: Clearbit API key (secret key from dashboard)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

CLEARBIT_PERSON_BASE = "https://person.clearbit.com/v2"
CLEARBIT_COMPANY_BASE = "https://company.clearbit.com/v2"
CLEARBIT_PROSPECTOR_BASE = "https://prospector.clearbit.com/v2"
CLEARBIT_REVEAL_BASE = "https://reveal.clearbit.com/v1"
CLEARBIT_RISK_BASE = "https://risk.clearbit.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "clearbit_enrich_person",
        "description": "Enrich a person record using their email address to get name, title, company, social profiles, and more",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Person's email address to enrich"},
                "webhook_url": {"type": "string", "description": "Optional webhook URL for async enrichment"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "clearbit_enrich_company",
        "description": "Enrich a company record using its domain to get description, employees, funding, tech stack, and more",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Company website domain, e.g. 'stripe.com'"},
            },
            "required": ["domain"],
        },
    },
    {
        "name": "clearbit_find_email",
        "description": "Find a person's work email address given their name and company domain",
        "parameters": {
            "type": "object",
            "properties": {
                "given_name": {"type": "string", "description": "Person's first name"},
                "family_name": {"type": "string", "description": "Person's last name"},
                "domain": {"type": "string", "description": "Company domain, e.g. 'acme.com'"},
            },
            "required": ["given_name", "family_name", "domain"],
        },
    },
    {
        "name": "clearbit_reveal_company_from_ip",
        "description": "Identify the company of a website visitor from their IP address",
        "parameters": {
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "IPv4 or IPv6 address of the visitor"},
            },
            "required": ["ip"],
        },
    },
    {
        "name": "clearbit_search_companies",
        "description": "Search and filter companies in the Clearbit database by various attributes",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Full-text search query"},
                "employee_range": {"type": "string", "description": "Employee count range, e.g. '1,10'"},
                "industry_group": {"type": "string", "description": "Industry group filter"},
                "country": {"type": "string", "description": "ISO 3166-1 alpha-2 country code"},
                "limit": {"type": "integer", "description": "Max results (1-100)", "default": 20},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    {
        "name": "clearbit_get_risk_score",
        "description": "Get a fraud risk score for an email address with signals like disposable email, free provider, and more",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email address to assess for risk"},
                "ip": {"type": "string", "description": "Optional IP address for additional risk signals"},
                "name": {"type": "string", "description": "Optional full name for identity verification"},
            },
            "required": ["email"],
        },
    },
]


def _auth(api_key: str) -> tuple[str, str]:
    return (api_key, "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("CLEARBIT_API_KEY", "")
    if not api_key:
        return {"error": "CLEARBIT_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(auth=_auth(api_key), timeout=30.0) as c:
            if tool_name == "clearbit_enrich_person":
                params: dict[str, Any] = {"email": arguments["email"]}
                if "webhook_url" in arguments:
                    params["webhook_url"] = arguments["webhook_url"]
                r = await c.get(f"{CLEARBIT_PERSON_BASE}/people/find", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "clearbit_enrich_company":
                r = await c.get(
                    f"{CLEARBIT_COMPANY_BASE}/companies/find",
                    params={"domain": arguments["domain"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "clearbit_find_email":
                params = {
                    "given_name": arguments["given_name"],
                    "family_name": arguments["family_name"],
                    "domain": arguments["domain"],
                }
                r = await c.get(f"{CLEARBIT_PROSPECTOR_BASE}/people/find", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "clearbit_reveal_company_from_ip":
                r = await c.get(
                    f"{CLEARBIT_REVEAL_BASE}/companies/find",
                    params={"ip": arguments["ip"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "clearbit_search_companies":
                params = {
                    "limit": arguments.get("limit", 20),
                    "page": arguments.get("page", 1),
                }
                if "query" in arguments:
                    params["query"] = arguments["query"]
                if "employee_range" in arguments:
                    params["employee_range"] = arguments["employee_range"]
                if "industry_group" in arguments:
                    params["industry_group"] = arguments["industry_group"]
                if "country" in arguments:
                    params["country"] = arguments["country"]
                r = await c.get(f"{CLEARBIT_COMPANY_BASE}/companies/search", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "clearbit_get_risk_score":
                params = {"email": arguments["email"]}
                if "ip" in arguments:
                    params["ip"] = arguments["ip"]
                if "name" in arguments:
                    params["name"] = arguments["name"]
                r = await c.get(f"{CLEARBIT_RISK_BASE}/calculate", params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("clearbit_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
