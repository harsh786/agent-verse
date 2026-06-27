"""DocuSign MCP server — eSignature envelopes and signing workflows.

Environment:
  DOCUSIGN_ACCESS_TOKEN: OAuth 2.0 Bearer token
  DOCUSIGN_ACCOUNT_ID:   DocuSign Account ID
  DOCUSIGN_BASE_URL:     API base URL (e.g. https://demo.docusign.net/restapi or https://www.docusign.net/restapi)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "docusign_list_envelopes",
        "description": "List DocuSign envelopes with optional status filter",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status: sent, delivered, signed, completed, declined, voided, created",
                },
                "from_date": {"type": "string", "description": "ISO 8601 datetime filter"},
                "to_date": {"type": "string", "description": "ISO 8601 datetime filter"},
                "count": {"type": "integer", "default": 20},
                "start_position": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "docusign_get_envelope",
        "description": "Get details about a specific DocuSign envelope",
        "parameters": {
            "type": "object",
            "properties": {
                "envelope_id": {"type": "string"},
                "include": {
                    "type": "string",
                    "description": "Comma-separated fields: recipients, documents, certificates",
                },
            },
            "required": ["envelope_id"],
        },
    },
    {
        "name": "docusign_create_envelope",
        "description": "Create a DocuSign envelope to send for signature",
        "parameters": {
            "type": "object",
            "properties": {
                "email_subject": {"type": "string"},
                "email_blurb": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["created", "sent"],
                    "default": "created",
                    "description": "Use 'sent' to immediately send for signature",
                },
                "documents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "documentBase64": {"type": "string"},
                            "documentId": {"type": "string"},
                            "fileExtension": {"type": "string"},
                            "name": {"type": "string"},
                        },
                    },
                    "description": "Documents to include (base64 encoded)",
                },
                "recipients": {
                    "type": "object",
                    "description": "Recipients object with signers, cc, etc.",
                },
            },
            "required": ["email_subject", "documents", "recipients"],
        },
    },
    {
        "name": "docusign_get_signing_url",
        "description": "Generate an embedded signing URL for a recipient",
        "parameters": {
            "type": "object",
            "properties": {
                "envelope_id": {"type": "string"},
                "client_user_id": {"type": "string", "description": "Unique client user identifier"},
                "email": {"type": "string", "description": "Signer's email address"},
                "name": {"type": "string", "description": "Signer's name"},
                "return_url": {"type": "string", "description": "URL to redirect after signing"},
                "authentication_method": {
                    "type": "string",
                    "default": "none",
                    "enum": ["none", "email", "phone", "idcheck", "kba"],
                },
            },
            "required": ["envelope_id", "client_user_id", "email", "name", "return_url"],
        },
    },
    {
        "name": "docusign_send_envelope",
        "description": "Send a draft envelope (change status from 'created' to 'sent')",
        "parameters": {
            "type": "object",
            "properties": {
                "envelope_id": {"type": "string"},
            },
            "required": ["envelope_id"],
        },
    },
    {
        "name": "docusign_void_envelope",
        "description": "Void a sent or delivered DocuSign envelope",
        "parameters": {
            "type": "object",
            "properties": {
                "envelope_id": {"type": "string"},
                "voided_reason": {"type": "string"},
            },
            "required": ["envelope_id", "voided_reason"],
        },
    },
]


def _base() -> str:
    base = os.getenv("DOCUSIGN_BASE_URL", "https://www.docusign.net/restapi").rstrip("/")
    account_id = os.getenv("DOCUSIGN_ACCOUNT_ID", "")
    return f"{base}/v2.1/accounts/{account_id}"


def _headers() -> dict[str, str]:
    token = os.getenv("DOCUSIGN_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("DOCUSIGN_ACCESS_TOKEN", "")
    account_id = os.getenv("DOCUSIGN_ACCOUNT_ID", "")
    if not token or not account_id:
        return {"error": "DOCUSIGN_ACCESS_TOKEN and DOCUSIGN_ACCOUNT_ID must be configured"}

    base = _base()

    try:
        async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as c:
            if tool_name == "docusign_list_envelopes":
                params: dict[str, Any] = {
                    "count": arguments.get("count", 20),
                    "start_position": arguments.get("start_position", 0),
                }
                if status := arguments.get("status"):
                    params["status"] = status
                if from_date := arguments.get("from_date"):
                    params["from_date"] = from_date
                if to_date := arguments.get("to_date"):
                    params["to_date"] = to_date
                r = await c.get(f"{base}/envelopes", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "docusign_get_envelope":
                params = {}
                if include := arguments.get("include"):
                    params["include"] = include
                r = await c.get(f"{base}/envelopes/{arguments['envelope_id']}", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "docusign_create_envelope":
                payload: dict[str, Any] = {
                    "emailSubject": arguments["email_subject"],
                    "status": arguments.get("status", "created"),
                    "documents": arguments["documents"],
                    "recipients": arguments["recipients"],
                }
                if blurb := arguments.get("email_blurb"):
                    payload["emailBlurb"] = blurb
                r = await c.post(f"{base}/envelopes", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "docusign_get_signing_url":
                eid = arguments["envelope_id"]
                payload = {
                    "clientUserId": arguments["client_user_id"],
                    "email": arguments["email"],
                    "userName": arguments["name"],
                    "returnUrl": arguments["return_url"],
                    "authenticationMethod": arguments.get("authentication_method", "none"),
                }
                r = await c.post(f"{base}/envelopes/{eid}/views/recipient", json=payload)
                r.raise_for_status()
                return r.json()

            elif tool_name == "docusign_send_envelope":
                r = await c.put(
                    f"{base}/envelopes/{arguments['envelope_id']}",
                    json={"status": "sent"},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "docusign_void_envelope":
                r = await c.put(
                    f"{base}/envelopes/{arguments['envelope_id']}",
                    json={
                        "status": "voided",
                        "voidedReason": arguments["voided_reason"],
                    },
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("docusign_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
