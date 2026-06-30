"""SignNow MCP server — electronic signature management.

Environment:
  SIGNNOW_ACCESS_TOKEN: SignNow OAuth2 access token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

SIGNNOW_BASE = "https://api.signnow.com"

TOOL_DEFINITIONS = [
    {
        "name": "signnow_create_document",
        "description": "Upload a document to SignNow from a URL",
        "parameters": {
            "type": "object",
            "properties": {
                "file_url": {"type": "string", "description": "Public URL of the PDF to upload"},
                "name": {"type": "string", "description": "Document display name"},
            },
            "required": ["file_url"],
        },
    },
    {
        "name": "signnow_send_invite",
        "description": "Send a signing invite for a document",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "to": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "email": {"type": "string"},
                            "role": {"type": "string"},
                            "order": {"type": "integer"},
                        },
                    },
                },
                "subject": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["document_id", "to"],
        },
    },
    {
        "name": "signnow_list_documents",
        "description": "List documents in the SignNow account",
        "parameters": {
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "default": 20},
                "page": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "signnow_download_document",
        "description": "Get a download link for a SignNow document",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["pdf", "zip"],
                    "default": "pdf",
                },
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "signnow_check_document_status",
        "description": "Check the signing status of a document",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "signnow_create_template",
        "description": "Create a signing template from an existing SignNow document",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Source document ID"},
                "name": {"type": "string", "description": "Template name"},
            },
            "required": ["document_id", "name"],
        },
    },
]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("SIGNNOW_ACCESS_TOKEN", "")
    if not token:
        return {"error": "SIGNNOW_ACCESS_TOKEN not configured"}

    hdrs = _headers(token)

    async with httpx.AsyncClient(timeout=60.0) as c:
        try:
            if tool_name == "signnow_create_document":
                r = await c.post(
                    f"{SIGNNOW_BASE}/document/fieldextract",
                    headers={**hdrs, "Content-Type": "application/json"},
                    json={
                        "url": arguments["file_url"],
                        "name": arguments.get("name", ""),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "document_id": data.get("id"),
                    "name": data.get("document_name"),
                    "created": True,
                }

            elif tool_name == "signnow_send_invite":
                body: dict[str, Any] = {
                    "to": [
                        {
                            "email": recip.get("email"),
                            "role": recip.get("role", "Signer"),
                            "order": recip.get("order", 1),
                        }
                        for recip in arguments["to"]
                    ],
                    "from": "",
                    "subject": arguments.get("subject", "Please sign this document"),
                    "message": arguments.get("message", ""),
                }
                r = await c.post(
                    f"{SIGNNOW_BASE}/document/{arguments['document_id']}/invite",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return {"sent": True, "document_id": arguments["document_id"]}

            elif tool_name == "signnow_list_documents":
                r = await c.get(
                    f"{SIGNNOW_BASE}/user/documentsv2",
                    headers=hdrs,
                    params={
                        "per_page": arguments.get("per_page", 20),
                        "page": arguments.get("page", 0),
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "documents": [
                        {
                            "id": doc.get("id"),
                            "name": doc.get("document_name"),
                            "status": doc.get("status"),
                            "updated": doc.get("updated"),
                        }
                        for doc in data
                    ]
                    if isinstance(data, list)
                    else {"raw": data}
                }

            elif tool_name == "signnow_download_document":
                r = await c.get(
                    f"{SIGNNOW_BASE}/document/{arguments['document_id']}/download/link",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "document_id": arguments["document_id"],
                    "download_url": data.get("link"),
                }

            elif tool_name == "signnow_check_document_status":
                r = await c.get(
                    f"{SIGNNOW_BASE}/document/{arguments['document_id']}",
                    headers=hdrs,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "document_id": arguments["document_id"],
                    "status": data.get("status"),
                    "updated": data.get("updated"),
                    "signatures": len(data.get("signatures", [])),
                    "pending_invites": len(data.get("pending_invites", [])),
                }

            elif tool_name == "signnow_create_template":
                r = await c.post(
                    f"{SIGNNOW_BASE}/template",
                    headers=hdrs,
                    json={
                        "document_id": arguments["document_id"],
                        "document_name": arguments["name"],
                    },
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "template_id": data.get("id"),
                    "name": arguments["name"],
                    "created": True,
                }

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("signnow_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
