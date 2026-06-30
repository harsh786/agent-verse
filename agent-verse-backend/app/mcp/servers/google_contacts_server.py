"""Google Contacts MCP server — manage contacts via the People API.

Environment:
  GOOGLE_ACCESS_TOKEN: OAuth2 access token with contacts.readwrite scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://people.googleapis.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "google_contacts_list_contacts",
        "description": "List all Google contacts for the authenticated user with optional field filtering",
        "parameters": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "description": "Number of contacts per page (max 1000)"},
                "page_token": {"type": "string", "description": "Pagination token from previous response"},
                "person_fields": {"type": "string", "description": "Comma-separated list of fields (e.g. names,emailAddresses,phoneNumbers)"},
            },
        },
    },
    {
        "name": "google_contacts_create_contact",
        "description": "Create a new Google contact",
        "parameters": {
            "type": "object",
            "properties": {
                "given_name": {"type": "string", "description": "First name of the contact"},
                "family_name": {"type": "string", "description": "Last name of the contact"},
                "email": {"type": "string", "description": "Primary email address"},
                "phone": {"type": "string", "description": "Primary phone number"},
                "organization": {"type": "string", "description": "Company or organization name"},
            },
            "required": ["given_name"],
        },
    },
    {
        "name": "google_contacts_update_contact",
        "description": "Update an existing Google contact's information",
        "parameters": {
            "type": "object",
            "properties": {
                "resource_name": {"type": "string", "description": "Contact resource name (e.g. people/c1234567)"},
                "given_name": {"type": "string", "description": "Updated first name"},
                "family_name": {"type": "string", "description": "Updated last name"},
                "email": {"type": "string", "description": "Updated email address"},
                "phone": {"type": "string", "description": "Updated phone number"},
                "etag": {"type": "string", "description": "ETag for optimistic concurrency"},
            },
            "required": ["resource_name"],
        },
    },
    {
        "name": "google_contacts_delete_contact",
        "description": "Delete a Google contact by resource name",
        "parameters": {
            "type": "object",
            "properties": {
                "resource_name": {"type": "string", "description": "Contact resource name (e.g. people/c1234567)"},
            },
            "required": ["resource_name"],
        },
    },
    {
        "name": "google_contacts_search_contacts",
        "description": "Search contacts by name, email, or phone number",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "page_size": {"type": "integer", "description": "Maximum number of results"},
                "read_mask": {"type": "string", "description": "Fields to return (default: names,emailAddresses)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "google_contacts_get_contact",
        "description": "Get a specific contact by resource name with requested fields",
        "parameters": {
            "type": "object",
            "properties": {
                "resource_name": {"type": "string", "description": "Contact resource name (e.g. people/c1234567)"},
                "person_fields": {"type": "string", "description": "Fields to include in response"},
            },
            "required": ["resource_name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "GOOGLE_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "google_contacts_list_contacts":
                params: dict[str, Any] = {
                    "personFields": arguments.get("person_fields", "names,emailAddresses,phoneNumbers"),
                }
                if "page_size" in arguments:
                    params["pageSize"] = arguments["page_size"]
                if "page_token" in arguments:
                    params["pageToken"] = arguments["page_token"]
                r = await client.get(f"{BASE_URL}/people/me/connections", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "google_contacts_create_contact":
                payload: dict[str, Any] = {}
                names: dict[str, str] = {}
                if "given_name" in arguments:
                    names["givenName"] = arguments["given_name"]
                if "family_name" in arguments:
                    names["familyName"] = arguments["family_name"]
                if names:
                    payload["names"] = [names]
                if "email" in arguments:
                    payload["emailAddresses"] = [{"value": arguments["email"]}]
                if "phone" in arguments:
                    payload["phoneNumbers"] = [{"value": arguments["phone"]}]
                if "organization" in arguments:
                    payload["organizations"] = [{"name": arguments["organization"]}]
                r = await client.post(f"{BASE_URL}/people:createContact", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "google_contacts_update_contact":
                resource_name = arguments["resource_name"]
                payload = {}
                if "etag" in arguments:
                    payload["etag"] = arguments["etag"]
                names = {}
                if "given_name" in arguments:
                    names["givenName"] = arguments["given_name"]
                if "family_name" in arguments:
                    names["familyName"] = arguments["family_name"]
                if names:
                    payload["names"] = [names]
                if "email" in arguments:
                    payload["emailAddresses"] = [{"value": arguments["email"]}]
                if "phone" in arguments:
                    payload["phoneNumbers"] = [{"value": arguments["phone"]}]
                update_fields = ",".join(k for k in ["names", "emailAddresses", "phoneNumbers"] if k in payload)
                r = await client.patch(
                    f"{BASE_URL}/{resource_name}:updateContact",
                    headers=headers,
                    params={"updatePersonFields": update_fields},
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_contacts_delete_contact":
                resource_name = arguments["resource_name"]
                r = await client.delete(
                    f"{BASE_URL}/{resource_name}:deleteContact",
                    headers=headers,
                )
                r.raise_for_status()
                return {"deleted": True}

            if tool_name == "google_contacts_search_contacts":
                params = {
                    "query": arguments["query"],
                    "readMask": arguments.get("read_mask", "names,emailAddresses,phoneNumbers"),
                }
                if "page_size" in arguments:
                    params["pageSize"] = arguments["page_size"]
                r = await client.get(f"{BASE_URL}/people:searchContacts", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "google_contacts_get_contact":
                resource_name = arguments["resource_name"]
                params = {"personFields": arguments.get("person_fields", "names,emailAddresses,phoneNumbers")}
                r = await client.get(f"{BASE_URL}/{resource_name}", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
