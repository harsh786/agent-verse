"""FullContact MCP server — person/company enrichment, tag management, and contact creation.

Environment variables:
  FULLCONTACT_API_KEY: FullContact API key
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

FULLCONTACT_BASE = "https://api.fullcontact.com/v3"

TOOL_DEFINITIONS = [
    {
        "name": "fullcontact_enrich_person",
        "description": "Enrich a person record using email, phone, or social profile to get demographics, employment, and social data",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Person's email address"},
                "phone": {"type": "string", "description": "Phone number in E.164 format"},
                "twitter": {"type": "string", "description": "Twitter handle (without @)"},
                "linkedin": {"type": "string", "description": "LinkedIn profile URL"},
                "placekey": {"type": "string", "description": "Physical address placekey"},
                "webhookUrl": {"type": "string", "description": "Webhook URL for async responses"},
            },
        },
    },
    {
        "name": "fullcontact_enrich_company",
        "description": "Enrich a company record using its domain or name to get description, metrics, tech stack, and social profiles",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Company website domain, e.g. 'fullcontact.com'"},
                "companyName": {"type": "string", "description": "Company name (used if domain unavailable)"},
                "webhookUrl": {"type": "string"},
            },
        },
    },
    {
        "name": "fullcontact_get_tags",
        "description": "Get all tags associated with a person record in FullContact",
        "parameters": {
            "type": "object",
            "properties": {
                "recordId": {"type": "string", "description": "FullContact record ID"},
            },
            "required": ["recordId"],
        },
    },
    {
        "name": "fullcontact_add_tag",
        "description": "Add a tag to a person record in FullContact",
        "parameters": {
            "type": "object",
            "properties": {
                "recordId": {"type": "string", "description": "FullContact person record ID"},
                "tagKey": {"type": "string", "description": "Tag key/name"},
                "tagValue": {"type": "string", "description": "Tag value"},
            },
            "required": ["recordId", "tagKey"],
        },
    },
    {
        "name": "fullcontact_create_person",
        "description": "Create or update a person record in your FullContact identity graph",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "fullName": {"type": "string", "description": "Full name, e.g. 'Jane Smith'"},
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "organization": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["email"],
        },
    },
    {
        "name": "fullcontact_search_contacts",
        "description": "Search for person records in your FullContact identity graph",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "twitter": {"type": "string"},
                "recordIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of FullContact record IDs",
                },
            },
        },
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("FULLCONTACT_API_KEY", "")
    if not api_key:
        return {"error": "FULLCONTACT_API_KEY not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=FULLCONTACT_BASE, headers=_headers(api_key), timeout=30.0
        ) as c:
            if tool_name == "fullcontact_enrich_person":
                body: dict[str, Any] = {}
                if "email" in arguments:
                    body.setdefault("emails", []).append(arguments["email"])
                if "phone" in arguments:
                    body.setdefault("phones", []).append(arguments["phone"])
                if "twitter" in arguments:
                    body.setdefault("twitter", []).append(arguments["twitter"])
                if "linkedin" in arguments:
                    body.setdefault("linkedin", []).append(arguments["linkedin"])
                if "webhookUrl" in arguments:
                    body["webhookUrl"] = arguments["webhookUrl"]
                r = await c.post("/person.enrich", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "fullcontact_enrich_company":
                body = {}
                if "domain" in arguments:
                    body["domain"] = arguments["domain"]
                if "companyName" in arguments:
                    body["companyName"] = arguments["companyName"]
                if "webhookUrl" in arguments:
                    body["webhookUrl"] = arguments["webhookUrl"]
                r = await c.post("/company.enrich", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "fullcontact_get_tags":
                r = await c.post(
                    "/tags.get",
                    json={"recordId": arguments["recordId"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "fullcontact_add_tag":
                body = {
                    "recordId": arguments["recordId"],
                    "tags": [{"key": arguments["tagKey"], "value": arguments.get("tagValue", "")}],
                }
                r = await c.post("/tags.create", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "fullcontact_create_person":
                body = {}
                if "email" in arguments:
                    body["emails"] = [arguments["email"]]
                if "phone" in arguments:
                    body["phones"] = [arguments["phone"]]
                name_fields: dict[str, Any] = {}
                for k in ("fullName", "firstName", "lastName"):
                    if k in arguments:
                        name_fields[k] = arguments[k]
                if name_fields:
                    body["name"] = name_fields
                for k in ("organization", "title"):
                    if k in arguments:
                        body[k] = arguments[k]
                r = await c.post("/person.create", json=body)
                r.raise_for_status()
                return r.json()

            elif tool_name == "fullcontact_search_contacts":
                body = {}
                if "email" in arguments:
                    body["emails"] = [arguments["email"]]
                if "phone" in arguments:
                    body["phones"] = [arguments["phone"]]
                if "twitter" in arguments:
                    body["twitter"] = [arguments["twitter"]]
                if "recordIds" in arguments:
                    body["recordIds"] = arguments["recordIds"]
                r = await c.post("/person.search", json=body)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("fullcontact_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
