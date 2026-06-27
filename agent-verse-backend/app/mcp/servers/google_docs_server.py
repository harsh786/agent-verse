"""Google Docs MCP server — create, read, and update Google Documents.

Environment variables (one required):
  GOOGLE_ACCESS_TOKEN:         OAuth2 bearer token
  GOOGLE_SERVICE_ACCOUNT_JSON: JSON string of a service-account key file
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

DOCS_BASE = "https://docs.googleapis.com/v1"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"

TOOL_DEFINITIONS = [
    {
        "name": "docs_get_document",
        "description": "Retrieve the full content and metadata of a Google Doc",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Google Doc document ID"},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "docs_create_document",
        "description": "Create a new Google Document with an optional title",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title of the new document"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "docs_batch_update",
        "description": "Apply a list of update requests to a Google Document (insert text, apply styles, etc.)",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "requests": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Google Docs API Request objects",
                },
            },
            "required": ["document_id", "requests"],
        },
    },
    {
        "name": "docs_insert_text",
        "description": "Insert plain text at a specific index in a Google Document",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "text": {"type": "string", "description": "Text to insert"},
                "index": {
                    "type": "integer",
                    "default": 1,
                    "description": "Character index to insert at (1 = start of body)",
                },
            },
            "required": ["document_id", "text"],
        },
    },
    {
        "name": "docs_export",
        "description": "Export a Google Document to PDF, DOCX, or plain text via the Drive API",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "mime_type": {
                    "type": "string",
                    "enum": [
                        "application/pdf",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "text/plain",
                    ],
                    "default": "application/pdf",
                    "description": "Export format",
                },
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "docs_replace_text",
        "description": "Find and replace all occurrences of a text string in a Google Document",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "find": {"type": "string", "description": "Text to search for"},
                "replace": {"type": "string", "description": "Replacement text"},
                "match_case": {"type": "boolean", "default": False},
            },
            "required": ["document_id", "find", "replace"],
        },
    },
    {
        "name": "docs_get_text_content",
        "description": "Extract all plain text from a Google Document",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
            },
            "required": ["document_id"],
        },
    },
]


def _google_token() -> str:
    direct = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if direct:
        return direct
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if sa_json:
        try:
            from google.auth.transport.requests import Request  # type: ignore[import]
            from google.oauth2 import service_account  # type: ignore[import]

            creds = service_account.Credentials.from_service_account_info(
                json.loads(sa_json),
                scopes=[
                    "https://www.googleapis.com/auth/documents",
                    "https://www.googleapis.com/auth/drive",
                ],
            )
            creds.refresh(Request())
            return creds.token  # type: ignore[return-value]
        except Exception:
            logger.debug("google_service_account_refresh_failed", exc_info=True)
    return ""


def _extract_text(doc: dict[str, Any]) -> str:
    """Pull all text runs from a Docs JSON response into a flat string."""
    parts: list[str] = []
    for elem in doc.get("body", {}).get("content", []):
        for p_elem in elem.get("paragraph", {}).get("elements", []):
            text_run = p_elem.get("textRun", {})
            parts.append(text_run.get("content", ""))
    return "".join(parts)


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = _google_token()
    if not token:
        return {"error": "GOOGLE_ACCESS_TOKEN or GOOGLE_SERVICE_ACCOUNT_JSON required"}

    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    did = arguments.get("document_id", "")

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "docs_get_document":
                r = await c.get(f"{DOCS_BASE}/documents/{did}", headers=hdrs)
                r.raise_for_status()
                data = r.json()
                return {
                    "document_id": data["documentId"],
                    "title": data.get("title", ""),
                    "revision_id": data.get("revisionId", ""),
                    "body": data.get("body", {}),
                }

            elif tool_name == "docs_create_document":
                r = await c.post(
                    f"{DOCS_BASE}/documents",
                    headers=hdrs,
                    json={"title": arguments["title"]},
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "document_id": data["documentId"],
                    "title": data.get("title", ""),
                    "revision_id": data.get("revisionId", ""),
                }

            elif tool_name == "docs_batch_update":
                r = await c.post(
                    f"{DOCS_BASE}/documents/{did}:batchUpdate",
                    headers=hdrs,
                    json={"requests": arguments["requests"]},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "docs_insert_text":
                index = arguments.get("index", 1)
                r = await c.post(
                    f"{DOCS_BASE}/documents/{did}:batchUpdate",
                    headers=hdrs,
                    json={
                        "requests": [
                            {
                                "insertText": {
                                    "location": {"index": index},
                                    "text": arguments["text"],
                                }
                            }
                        ]
                    },
                )
                r.raise_for_status()
                return {"success": True, "document_id": did}

            elif tool_name == "docs_export":
                mime = arguments.get("mime_type", "application/pdf")
                r = await c.get(
                    f"{DRIVE_BASE}/files/{did}/export",
                    headers=hdrs,
                    params={"mimeType": mime},
                )
                r.raise_for_status()
                return {
                    "exported": True,
                    "mime_type": mime,
                    "size_bytes": len(r.content),
                    "content_base64": __import__("base64").b64encode(r.content).decode(),
                }

            elif tool_name == "docs_replace_text":
                r = await c.post(
                    f"{DOCS_BASE}/documents/{did}:batchUpdate",
                    headers=hdrs,
                    json={
                        "requests": [
                            {
                                "replaceAllText": {
                                    "containsText": {
                                        "text": arguments["find"],
                                        "matchCase": arguments.get("match_case", False),
                                    },
                                    "replaceText": arguments["replace"],
                                }
                            }
                        ]
                    },
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "docs_get_text_content":
                r = await c.get(f"{DOCS_BASE}/documents/{did}", headers=hdrs)
                r.raise_for_status()
                data = r.json()
                return {
                    "document_id": data["documentId"],
                    "title": data.get("title", ""),
                    "text": _extract_text(data),
                }

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("docs_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
