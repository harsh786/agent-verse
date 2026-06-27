"""Confluence MCP server — Confluence Cloud REST API integration.

Environment variables:
  CONFLUENCE_BASE_URL: Confluence base URL (e.g. https://mycompany.atlassian.net)
  CONFLUENCE_EMAIL: Atlassian account email
  CONFLUENCE_API_TOKEN: Atlassian API token
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "confluence_search",
        "description": "Search Confluence content using CQL (Confluence Query Language)",
        "parameters": {
            "type": "object",
            "properties": {
                "cql": {
                    "type": "string",
                    "description": "CQL query, e.g. 'space = MYSPACE AND type = page AND title ~ \"Architecture\"'",
                },
                "limit": {"type": "integer", "default": 25},
                "start": {"type": "integer", "default": 0},
            },
            "required": ["cql"],
        },
    },
    {
        "name": "confluence_get_page",
        "description": "Get a Confluence page by its ID, including body content and version",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Confluence page ID"},
                "expand": {
                    "type": "string",
                    "default": "body.storage,version,space,ancestors",
                    "description": "Comma-separated list of properties to expand",
                },
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "confluence_create_page",
        "description": "Create a new Confluence page in a space",
        "parameters": {
            "type": "object",
            "properties": {
                "space_key": {"type": "string", "description": "Space key where the page will be created"},
                "title": {"type": "string"},
                "body": {
                    "type": "string",
                    "description": "Page body in Confluence Storage Format (XHTML-based)",
                },
                "parent_page_id": {
                    "type": "string",
                    "description": "Optional parent page ID to nest under",
                },
            },
            "required": ["space_key", "title", "body"],
        },
    },
    {
        "name": "confluence_update_page",
        "description": "Update an existing Confluence page (increments version automatically)",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "New body in Confluence Storage Format"},
                "version_number": {
                    "type": "integer",
                    "description": "Current version number (required by API). Fetched automatically if omitted.",
                },
            },
            "required": ["page_id", "title", "body"],
        },
    },
    {
        "name": "confluence_list_spaces",
        "description": "List Confluence spaces accessible to the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25},
                "type": {
                    "type": "string",
                    "enum": ["global", "personal"],
                    "description": "Filter by space type",
                },
            },
        },
    },
    {
        "name": "confluence_add_comment",
        "description": "Add an inline comment to a Confluence page",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "body": {"type": "string", "description": "Comment text (plain text or storage format)"},
            },
            "required": ["page_id", "body"],
        },
    },
    {
        "name": "confluence_attach_file",
        "description": "Attach a file to a Confluence page by providing its content as base64",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "filename": {"type": "string"},
                "content_base64": {
                    "type": "string",
                    "description": "Base64-encoded file content",
                },
                "mime_type": {"type": "string", "default": "application/octet-stream"},
            },
            "required": ["page_id", "filename", "content_base64"],
        },
    },
]


def _confluence_auth() -> dict[str, str]:
    email = os.getenv("CONFLUENCE_EMAIL", "")
    token = os.getenv("CONFLUENCE_API_TOKEN", "")
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base = os.getenv("CONFLUENCE_BASE_URL", "").rstrip("/")
    if not base:
        return {"error": "CONFLUENCE_BASE_URL not configured"}

    headers = _confluence_auth()

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=30.0) as client:
        if tool_name == "confluence_search":
            params: dict[str, Any] = {
                "cql": arguments["cql"],
                "limit": arguments.get("limit", 25),
                "start": arguments.get("start", 0),
            }
            resp = await client.get("/wiki/rest/api/search", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "total": data.get("totalSize", 0),
                "results": [
                    {
                        "id": r.get("content", {}).get("id", ""),
                        "title": r.get("content", {}).get("title", ""),
                        "type": r.get("content", {}).get("type", ""),
                        "space": r.get("resultGlobalContainer", {}).get("title", ""),
                        "url": r.get("url", ""),
                        "excerpt": r.get("excerpt", ""),
                    }
                    for r in data.get("results", [])
                ],
            }

        elif tool_name == "confluence_get_page":
            page_id = arguments["page_id"]
            expand = arguments.get("expand", "body.storage,version,space,ancestors")
            resp = await client.get(
                f"/wiki/rest/api/content/{page_id}",
                params={"expand": expand},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data.get("id", ""),
                "title": data.get("title", ""),
                "type": data.get("type", ""),
                "status": data.get("status", ""),
                "version": data.get("version", {}).get("number", 0),
                "space_key": data.get("space", {}).get("key", ""),
                "body": data.get("body", {}).get("storage", {}).get("value", ""),
                "url": data.get("_links", {}).get("webui", ""),
            }

        elif tool_name == "confluence_create_page":
            payload: dict[str, Any] = {
                "type": "page",
                "title": arguments["title"],
                "space": {"key": arguments["space_key"]},
                "body": {
                    "storage": {
                        "value": arguments["body"],
                        "representation": "storage",
                    }
                },
            }
            if arguments.get("parent_page_id"):
                payload["ancestors"] = [{"id": arguments["parent_page_id"]}]
            resp = await client.post("/wiki/rest/api/content", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data.get("id", ""),
                "title": data.get("title", ""),
                "url": data.get("_links", {}).get("webui", ""),
                "version": data.get("version", {}).get("number", 0),
            }

        elif tool_name == "confluence_update_page":
            page_id = arguments["page_id"]
            version_number = arguments.get("version_number")
            if version_number is None:
                # Fetch current version
                v_resp = await client.get(
                    f"/wiki/rest/api/content/{page_id}",
                    params={"expand": "version"},
                )
                v_resp.raise_for_status()
                version_number = v_resp.json().get("version", {}).get("number", 1)

            payload = {
                "type": "page",
                "title": arguments["title"],
                "version": {"number": version_number + 1},
                "body": {
                    "storage": {
                        "value": arguments["body"],
                        "representation": "storage",
                    }
                },
            }
            resp = await client.put(f"/wiki/rest/api/content/{page_id}", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "id": data.get("id", ""),
                "title": data.get("title", ""),
                "version": data.get("version", {}).get("number", 0),
            }

        elif tool_name == "confluence_list_spaces":
            params = {"limit": arguments.get("limit", 25)}
            if arguments.get("type"):
                params["type"] = arguments["type"]
            resp = await client.get("/wiki/rest/api/space", params=params)
            resp.raise_for_status()
            data = resp.json()
            return {
                "spaces": [
                    {
                        "id": s.get("id", ""),
                        "key": s.get("key", ""),
                        "name": s.get("name", ""),
                        "type": s.get("type", ""),
                        "status": s.get("status", ""),
                    }
                    for s in data.get("results", [])
                ]
            }

        elif tool_name == "confluence_add_comment":
            page_id = arguments["page_id"]
            payload = {
                "type": "comment",
                "container": {"id": page_id, "type": "page"},
                "body": {
                    "storage": {
                        "value": f"<p>{arguments['body']}</p>",
                        "representation": "storage",
                    }
                },
            }
            resp = await client.post(
                f"/wiki/rest/api/content/{page_id}/child/comment",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return {"comment_id": data.get("id", ""), "created": True}

        elif tool_name == "confluence_attach_file":
            import base64 as b64_module

            page_id = arguments["page_id"]
            filename = arguments["filename"]
            file_bytes = b64_module.b64decode(arguments["content_base64"])
            mime_type = arguments.get("mime_type", "application/octet-stream")

            # Multipart upload — need custom headers without Content-Type override
            upload_headers = {k: v for k, v in headers.items() if k != "Content-Type"}
            upload_headers["X-Atlassian-Token"] = "no-check"

            files = {"file": (filename, file_bytes, mime_type)}
            resp = await client.post(
                f"/wiki/rest/api/content/{page_id}/child/attachment",
                files=files,
                headers=upload_headers,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [{}])
            first = results[0] if results else {}
            return {
                "attachment_id": first.get("id", ""),
                "filename": first.get("title", filename),
                "url": first.get("_links", {}).get("download", ""),
            }

        else:
            return {"error": f"Unknown tool: {tool_name}"}
