"""Mendeley MCP server — academic research platform, documents, and groups.

Environment:
  MENDELEY_ACCESS_TOKEN: Mendeley OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.mendeley.com"

TOOL_DEFINITIONS = [
    {
        "name": "mendeley_list_documents",
        "description": "List documents in the authenticated user's Mendeley library",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum documents to return"},
                "offset": {"type": "integer", "description": "Pagination offset"},
                "sort": {"type": "string", "description": "Sort by: created, last_modified"},
                "order": {"type": "string", "description": "Sort order: asc or desc"},
            },
        },
    },
    {
        "name": "mendeley_create_document",
        "description": "Add a new reference document to the Mendeley library",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Document/paper title"},
                "type": {"type": "string", "description": "Document type: journal_article, book, thesis, etc."},
                "year": {"type": "integer", "description": "Publication year"},
                "authors": {
                    "type": "array",
                    "description": "Authors as objects with first_name and last_name",
                    "items": {"type": "object"},
                },
                "abstract": {"type": "string", "description": "Abstract text"},
                "source": {"type": "string", "description": "Journal or source name"},
            },
            "required": ["title", "type"],
        },
    },
    {
        "name": "mendeley_search_catalog",
        "description": "Search Mendeley's public catalog of academic papers",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"},
                "limit": {"type": "integer", "description": "Maximum results"},
                "field": {"type": "string", "description": "Field to search: title, author, abstract"},
                "min_year": {"type": "integer", "description": "Minimum publication year"},
                "max_year": {"type": "integer", "description": "Maximum publication year"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mendeley_list_groups",
        "description": "List Mendeley groups the authenticated user belongs to",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum groups"},
                "offset": {"type": "integer", "description": "Pagination offset"},
                "type": {"type": "string", "description": "Group type: normal, invite_only, public"},
            },
        },
    },
    {
        "name": "mendeley_list_annotations",
        "description": "List annotations and highlights on documents in the library",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Filter by document ID"},
                "limit": {"type": "integer", "description": "Maximum annotations"},
                "offset": {"type": "integer", "description": "Pagination offset"},
            },
        },
    },
    {
        "name": "mendeley_get_document_details",
        "description": "Get full details of a specific Mendeley document including metadata",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Mendeley document ID"},
            },
            "required": ["document_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("MENDELEY_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "MENDELEY_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "mendeley_list_documents":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(
                    f"{BASE_URL}/documents",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "mendeley_create_document":
                payload = {k: v for k, v in arguments.items() if v is not None}
                r = await client.post(f"{BASE_URL}/documents", headers=headers, json=payload)
                r.raise_for_status()
                return r.json()

            if tool_name == "mendeley_search_catalog":
                params: dict[str, Any] = {"query": arguments["query"]}
                if "limit" in arguments:
                    params["limit"] = arguments["limit"]
                if "field" in arguments:
                    params[arguments["field"]] = arguments["query"]
                    params.pop("query", None)
                r = await client.get(f"{BASE_URL}/search/catalog", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "mendeley_list_groups":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/groups", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "mendeley_list_annotations":
                params = {k: v for k, v in arguments.items() if v is not None}
                r = await client.get(f"{BASE_URL}/annotations", headers=headers, params=params)
                r.raise_for_status()
                return r.json()

            if tool_name == "mendeley_get_document_details":
                r = await client.get(
                    f"{BASE_URL}/documents/{arguments['document_id']}",
                    headers=headers,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
