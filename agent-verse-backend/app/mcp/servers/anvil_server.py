"""Anvil MCP server — PDF generation, form filling, and e-signature workflows.

Environment:
  ANVIL_API_KEY: Anvil API key for authentication
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://app.useanvil.com/api/v1"

TOOL_DEFINITIONS = [
    {
        "name": "anvil_fill_pdf",
        "description": "Fill a PDF template with data and generate a filled PDF document",
        "parameters": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "Anvil PDF template ID (cast ID)"},
                "data": {"type": "object", "description": "Key-value pairs mapping template fields to values"},
                "title": {"type": "string", "description": "Title for the generated document"},
            },
            "required": ["template_id", "data"],
        },
    },
    {
        "name": "anvil_generate_pdf",
        "description": "Generate a PDF from HTML or Markdown content",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "Content type: html or markdown"},
                "data": {"type": "object", "description": "HTML/Markdown content and styling options"},
                "title": {"type": "string", "description": "Title for the PDF document"},
            },
            "required": ["type", "data"],
        },
    },
    {
        "name": "anvil_create_etch_packet",
        "description": "Create an e-signature packet (Etch) for document signing workflow",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the signing packet"},
                "signers": {
                    "type": "array",
                    "description": "List of signer objects with name, email, and fields",
                    "items": {"type": "object"},
                },
                "files": {
                    "type": "array",
                    "description": "List of files to include in the packet",
                    "items": {"type": "object"},
                },
            },
            "required": ["name", "signers"],
        },
    },
    {
        "name": "anvil_list_weld_data_records",
        "description": "List data submissions from an Anvil webform (Weld)",
        "parameters": {
            "type": "object",
            "properties": {
                "weld_slug": {"type": "string", "description": "Slug of the Weld (webform)"},
                "page": {"type": "integer", "description": "Page number"},
            },
            "required": ["weld_slug"],
        },
    },
    {
        "name": "anvil_get_submission",
        "description": "Get details of a specific Anvil form submission or e-signature packet",
        "parameters": {
            "type": "object",
            "properties": {
                "submission_eid": {"type": "string", "description": "Submission or packet EID"},
            },
            "required": ["submission_eid"],
        },
    },
    {
        "name": "anvil_list_webhooks",
        "description": "List all webhooks configured for the Anvil account",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("ANVIL_API_KEY", "")
    if not api_key:
        return {"error": "ANVIL_API_KEY not configured"}

    auth = (api_key, "")
    gql_headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            if tool_name == "anvil_fill_pdf":
                r = await client.post(
                    f"{BASE_URL}/fill/{arguments['template_id']}.pdf",
                    auth=auth,
                    json={
                        "title": arguments.get("title", ""),
                        "data": arguments["data"],
                    },
                )
                r.raise_for_status()
                return {"pdf_bytes_length": len(r.content), "content_type": r.headers.get("content-type")}

            if tool_name == "anvil_generate_pdf":
                r = await client.post(
                    f"{BASE_URL}/generate-pdf",
                    auth=auth,
                    json={
                        "type": arguments["type"],
                        **arguments["data"],
                        "title": arguments.get("title", ""),
                    },
                )
                r.raise_for_status()
                return {"pdf_bytes_length": len(r.content)}

            if tool_name == "anvil_create_etch_packet":
                mutation = """
                mutation CreateEtchPacket($name: String!, $signers: [JSON]!, $files: [JSON]) {
                  createEtchPacket(
                    name: $name
                    signers: $signers
                    files: $files
                  ) { eid name status }
                }"""
                r = await client.post(
                    "https://app.useanvil.com/graphql",
                    auth=auth,
                    json={
                        "query": mutation,
                        "variables": {
                            "name": arguments["name"],
                            "signers": arguments["signers"],
                            "files": arguments.get("files", []),
                        },
                    },
                    headers=gql_headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "anvil_list_weld_data_records":
                query = """
                query WeldData($slug: String!, $page: Int) {
                  weldData(slug: $slug, page: $page) { eid data }
                }"""
                r = await client.post(
                    "https://app.useanvil.com/graphql",
                    auth=auth,
                    json={"query": query, "variables": {"slug": arguments["weld_slug"], "page": arguments.get("page", 1)}},
                    headers=gql_headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "anvil_get_submission":
                query = """
                query GetSubmission($eid: String!) {
                  weldDataByEid(eid: $eid) { eid data createdAt }
                }"""
                r = await client.post(
                    "https://app.useanvil.com/graphql",
                    auth=auth,
                    json={"query": query, "variables": {"eid": arguments["submission_eid"]}},
                    headers=gql_headers,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "anvil_list_webhooks":
                query = "query { webhooks { eid url status events } }"
                r = await client.post(
                    "https://app.useanvil.com/graphql",
                    auth=auth,
                    json={"query": query},
                    headers=gql_headers,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
