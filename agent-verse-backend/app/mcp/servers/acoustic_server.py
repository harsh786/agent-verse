"""Acoustic (IBM) Marketing Cloud MCP server — campaigns, contacts, and email sends.

Environment:
  ACOUSTIC_CLIENT_ID: Acoustic OAuth2 client ID
  ACOUSTIC_CLIENT_SECRET: Acoustic OAuth2 client secret
  ACOUSTIC_REFRESH_TOKEN: Acoustic OAuth2 refresh token
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api-campaign-us-6.goacoustic.com"

TOOL_DEFINITIONS = [
    {
        "name": "acoustic_list_campaigns",
        "description": "List marketing campaigns in the Acoustic account",
        "parameters": {
            "type": "object",
            "properties": {
                "page_number": {"type": "integer", "description": "Page number for pagination"},
                "page_size": {"type": "integer", "description": "Results per page"},
            },
        },
    },
    {
        "name": "acoustic_get_campaign_stats",
        "description": "Get performance statistics for a specific email campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "mailing_id": {"type": "string", "description": "Mailing/campaign ID"},
            },
            "required": ["mailing_id"],
        },
    },
    {
        "name": "acoustic_list_contacts",
        "description": "List contacts in an Acoustic database",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Database/list ID"},
                "page_number": {"type": "integer", "description": "Page number"},
                "page_size": {"type": "integer", "description": "Results per page"},
            },
            "required": ["list_id"],
        },
    },
    {
        "name": "acoustic_add_contact",
        "description": "Add or update a contact in an Acoustic database",
        "parameters": {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Target database ID"},
                "email": {"type": "string", "description": "Contact email address"},
                "columns": {"type": "object", "description": "Column name-value pairs"},
            },
            "required": ["list_id", "email"],
        },
    },
    {
        "name": "acoustic_send_mailing",
        "description": "Schedule or send an Acoustic email mailing",
        "parameters": {
            "type": "object",
            "properties": {
                "mailing_id": {"type": "string", "description": "Mailing template ID"},
                "schedule_date": {"type": "string", "description": "Send date/time in ISO format"},
            },
            "required": ["mailing_id"],
        },
    },
    {
        "name": "acoustic_list_databases",
        "description": "List all contact databases in the Acoustic account",
        "parameters": {
            "type": "object",
            "properties": {
                "visibility": {"type": "integer", "description": "0=private, 1=shared"},
                "list_type": {"type": "integer", "description": "0=database, 1=query, 2=test"},
            },
        },
    },
]


async def _get_token(client: httpx.AsyncClient) -> str:
    r = await client.post(
        f"{BASE_URL}/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": os.getenv("ACOUSTIC_CLIENT_ID", ""),
            "client_secret": os.getenv("ACOUSTIC_CLIENT_SECRET", ""),
            "refresh_token": os.getenv("ACOUSTIC_REFRESH_TOKEN", ""),
        },
    )
    r.raise_for_status()
    return r.json().get("access_token", "")


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    client_id = os.getenv("ACOUSTIC_CLIENT_ID", "")
    client_secret = os.getenv("ACOUSTIC_CLIENT_SECRET", "")
    refresh_token = os.getenv("ACOUSTIC_REFRESH_TOKEN", "")
    if not client_id or not client_secret or not refresh_token:
        return {"error": "ACOUSTIC_CLIENT_ID, ACOUSTIC_CLIENT_SECRET, and ACOUSTIC_REFRESH_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            token = await _get_token(client)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "text/xml;charset=utf-8"}

            def _xml_body(func_name: str, params_xml: str) -> str:
                return f"""<?xml version="1.0" encoding="UTF-8"?>
<Envelope><Body><{func_name}>{params_xml}</{func_name}></Body></Envelope>"""

            if tool_name == "acoustic_list_campaigns":
                xml = _xml_body("GetMailings", f"""
                <VISIBILITY>1</VISIBILITY>
                <MAILING_TYPE>1</MAILING_TYPE>
                <PAGE_SIZE>{arguments.get('page_size', 20)}</PAGE_SIZE>
                <PAGE_NUMBER>{arguments.get('page_number', 1)}</PAGE_NUMBER>""")
                r = await client.post(f"{BASE_URL}/XMLAPI", headers=headers, content=xml.encode())
                r.raise_for_status()
                return {"raw_xml": r.text[:2000]}

            if tool_name == "acoustic_get_campaign_stats":
                xml = _xml_body("GetAggregateTrackingForMailing", f"<MAILING_ID>{arguments['mailing_id']}</MAILING_ID>")
                r = await client.post(f"{BASE_URL}/XMLAPI", headers=headers, content=xml.encode())
                r.raise_for_status()
                return {"raw_xml": r.text[:2000]}

            if tool_name == "acoustic_list_contacts":
                xml = _xml_body("SelectRecipientData", f"""
                <LIST_ID>{arguments['list_id']}</LIST_ID>""")
                r = await client.post(f"{BASE_URL}/XMLAPI", headers=headers, content=xml.encode())
                r.raise_for_status()
                return {"raw_xml": r.text[:2000]}

            if tool_name == "acoustic_add_contact":
                cols = "".join(
                    f"<COLUMN><NAME>{k}</NAME><VALUE>{v}</VALUE></COLUMN>"
                    for k, v in arguments.get("columns", {}).items()
                )
                xml = _xml_body("AddRecipient", f"""
                <LIST_ID>{arguments['list_id']}</LIST_ID>
                <COLUMN><NAME>EMAIL</NAME><VALUE>{arguments['email']}</VALUE></COLUMN>
                {cols}""")
                r = await client.post(f"{BASE_URL}/XMLAPI", headers=headers, content=xml.encode())
                r.raise_for_status()
                return {"raw_xml": r.text[:2000]}

            if tool_name == "acoustic_send_mailing":
                xml = _xml_body("ScheduleMailing", f"""
                <TEMPLATE_ID>{arguments['mailing_id']}</TEMPLATE_ID>
                <SCHEDULED_DATE>{arguments.get('schedule_date', '')}</SCHEDULED_DATE>""")
                r = await client.post(f"{BASE_URL}/XMLAPI", headers=headers, content=xml.encode())
                r.raise_for_status()
                return {"raw_xml": r.text[:2000]}

            if tool_name == "acoustic_list_databases":
                xml = _xml_body("GetLists", f"""
                <VISIBILITY>{arguments.get('visibility', 1)}</VISIBILITY>
                <LIST_TYPE>{arguments.get('list_type', 0)}</LIST_TYPE>""")
                r = await client.post(f"{BASE_URL}/XMLAPI", headers=headers, content=xml.encode())
                r.raise_for_status()
                return {"raw_xml": r.text[:2000]}

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
