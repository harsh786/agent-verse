"""Microsoft Dynamics 365 CRM MCP server — accounts, contacts, and leads management.

Environment variables:
  DYNAMICS365_ACCESS_TOKEN: OAuth 2.0 Bearer access token for Dynamics 365
  DYNAMICS365_ORG_URL: Dynamics 365 organisation URL, e.g. https://myorg.crm.dynamics.com
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "dynamics365_list_accounts",
        "description": "List accounts (companies/organisations) in Dynamics 365 CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "select": {"type": "string", "description": "Comma-separated fields to return, e.g. 'name,telephone1,websiteurl'"},
                "filter": {"type": "string", "description": "OData $filter expression, e.g. \"name eq 'Contoso'\""},
                "top": {"type": "integer", "description": "Max records to return", "default": 50},
                "orderby": {"type": "string", "description": "OData $orderby expression"},
            },
        },
    },
    {
        "name": "dynamics365_create_account",
        "description": "Create a new account (company) in Dynamics 365 CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Account/company name"},
                "telephone1": {"type": "string", "description": "Primary phone number"},
                "websiteurl": {"type": "string"},
                "address1_city": {"type": "string"},
                "address1_country": {"type": "string"},
                "industrycode": {"type": "integer", "description": "Industry code (Dynamics 365 enum)"},
                "numberofemployees": {"type": "integer"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "dynamics365_list_contacts",
        "description": "List contacts (people) in Dynamics 365 CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "select": {"type": "string", "description": "Comma-separated fields, e.g. 'firstname,lastname,emailaddress1'"},
                "filter": {"type": "string", "description": "OData $filter expression"},
                "top": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "dynamics365_create_contact",
        "description": "Create a new contact (person) in Dynamics 365 CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "firstname": {"type": "string"},
                "lastname": {"type": "string"},
                "emailaddress1": {"type": "string", "description": "Primary email address"},
                "telephone1": {"type": "string"},
                "jobtitle": {"type": "string"},
                "parentcustomerid": {"type": "string", "description": "Associated account ID"},
            },
            "required": ["lastname"],
        },
    },
    {
        "name": "dynamics365_list_leads",
        "description": "List leads in Dynamics 365 CRM with optional filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "select": {"type": "string"},
                "filter": {"type": "string", "description": "OData $filter expression"},
                "top": {"type": "integer", "default": 50},
                "orderby": {"type": "string"},
            },
        },
    },
    {
        "name": "dynamics365_create_lead",
        "description": "Create a new lead in Dynamics 365 CRM",
        "parameters": {
            "type": "object",
            "properties": {
                "firstname": {"type": "string"},
                "lastname": {"type": "string"},
                "emailaddress1": {"type": "string"},
                "companyname": {"type": "string", "description": "Lead's company name"},
                "subject": {"type": "string", "description": "Lead topic/subject"},
                "telephone1": {"type": "string"},
                "jobtitle": {"type": "string"},
                "estimatedvalue": {"type": "number", "description": "Estimated deal value"},
            },
            "required": ["lastname", "subject"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
        "Accept": "application/json",
        "Prefer": "odata.include-annotations=OData.Community.Display.V1.FormattedValue",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("DYNAMICS365_ACCESS_TOKEN", "")
    if not token:
        return {"error": "DYNAMICS365_ACCESS_TOKEN not configured"}

    org_url = os.getenv("DYNAMICS365_ORG_URL", "").rstrip("/")
    if not org_url:
        return {"error": "DYNAMICS365_ORG_URL not configured"}

    base_url = f"{org_url}/api/data/v9.2"

    try:
        async with httpx.AsyncClient(
            base_url=base_url, headers=_headers(token), timeout=30.0
        ) as c:
            if tool_name == "dynamics365_list_accounts":
                params: dict[str, Any] = {"$top": arguments.get("top", 50)}
                if "select" in arguments:
                    params["$select"] = arguments["select"]
                if "filter" in arguments:
                    params["$filter"] = arguments["filter"]
                if "orderby" in arguments:
                    params["$orderby"] = arguments["orderby"]
                r = await c.get("/accounts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "dynamics365_create_account":
                body: dict[str, Any] = {"name": arguments["name"]}
                for k in ("telephone1", "websiteurl", "address1_city", "address1_country",
                          "industrycode", "numberofemployees"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/accounts", json=body)
                r.raise_for_status()
                location = r.headers.get("OData-EntityId", "")
                return {"created": True, "entity_url": location}

            elif tool_name == "dynamics365_list_contacts":
                params = {"$top": arguments.get("top", 50)}
                if "select" in arguments:
                    params["$select"] = arguments["select"]
                if "filter" in arguments:
                    params["$filter"] = arguments["filter"]
                r = await c.get("/contacts", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "dynamics365_create_contact":
                body = {"lastname": arguments["lastname"]}
                for k in ("firstname", "emailaddress1", "telephone1", "jobtitle", "parentcustomerid"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/contacts", json=body)
                r.raise_for_status()
                location = r.headers.get("OData-EntityId", "")
                return {"created": True, "entity_url": location}

            elif tool_name == "dynamics365_list_leads":
                params = {"$top": arguments.get("top", 50)}
                if "select" in arguments:
                    params["$select"] = arguments["select"]
                if "filter" in arguments:
                    params["$filter"] = arguments["filter"]
                if "orderby" in arguments:
                    params["$orderby"] = arguments["orderby"]
                r = await c.get("/leads", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "dynamics365_create_lead":
                body = {
                    "lastname": arguments["lastname"],
                    "subject": arguments["subject"],
                }
                for k in ("firstname", "emailaddress1", "companyname", "telephone1",
                          "jobtitle", "estimatedvalue"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/leads", json=body)
                r.raise_for_status()
                location = r.headers.get("OData-EntityId", "")
                return {"created": True, "entity_url": location}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("dynamics365_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
