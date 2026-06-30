"""Facebook Lead Ads MCP server — Facebook Lead Ad forms, leads, ad accounts, and insights.

Environment:
  FACEBOOK_ACCESS_TOKEN: Facebook access token with ads_management and leads_retrieval permissions
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

BASE = "https://graph.facebook.com/v18.0"

TOOL_DEFINITIONS = [
    {
        "name": "facebook_lead_ads_list_lead_forms",
        "description": "List all lead ad forms for a Facebook Page",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Facebook Page ID owning the lead forms"},
                "status": {"type": "string", "description": "Filter by status: ACTIVE, ARCHIVED, DELETED"},
                "limit": {"type": "integer", "description": "Number of forms to return", "default": 25},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "facebook_lead_ads_get_leads",
        "description": "Get leads collected by a specific Facebook lead ad form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string", "description": "Lead form (leadgen form) ID"},
                "fields": {"type": "string", "description": "Fields: id,created_time,field_data", "default": "id,created_time,field_data"},
                "limit": {"type": "integer", "description": "Number of leads to return", "default": 50},
                "after": {"type": "string", "description": "Pagination cursor"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "facebook_lead_ads_get_form_details",
        "description": "Get detailed information about a Facebook lead ad form",
        "parameters": {
            "type": "object",
            "properties": {
                "form_id": {"type": "string", "description": "Lead form ID"},
                "fields": {"type": "string", "description": "Fields: id,name,status,questions,leads_count,created_time"},
            },
            "required": ["form_id"],
        },
    },
    {
        "name": "facebook_lead_ads_list_ad_accounts",
        "description": "List all Facebook ad accounts accessible to the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {
                "fields": {"type": "string", "description": "Fields: id,name,account_status,currency,timezone_name"},
                "limit": {"type": "integer", "description": "Number of ad accounts to return", "default": 25},
            },
        },
    },
    {
        "name": "facebook_lead_ads_create_custom_audience",
        "description": "Create a custom audience from leads for Facebook ad targeting",
        "parameters": {
            "type": "object",
            "properties": {
                "ad_account_id": {"type": "string", "description": "Facebook ad account ID (act_XXXXX)"},
                "name": {"type": "string", "description": "Audience name"},
                "description": {"type": "string", "description": "Audience description"},
                "subtype": {"type": "string", "description": "Audience subtype: CUSTOM, LOOKALIKE", "default": "CUSTOM"},
            },
            "required": ["ad_account_id", "name"],
        },
    },
    {
        "name": "facebook_lead_ads_get_insights",
        "description": "Get performance insights for a Facebook lead ad campaign or ad set",
        "parameters": {
            "type": "object",
            "properties": {
                "object_id": {"type": "string", "description": "Ad campaign ID, ad set ID, or ad account ID"},
                "fields": {
                    "type": "string",
                    "description": "Insight fields: impressions,reach,spend,leads,cpl,frequency",
                    "default": "impressions,reach,spend,leads",
                },
                "date_preset": {"type": "string", "description": "Date preset: today, yesterday, last_7d, last_30d, this_month"},
                "time_range": {
                    "type": "object",
                    "properties": {
                        "since": {"type": "string"},
                        "until": {"type": "string"},
                    },
                    "description": "Custom time range with since/until dates (YYYY-MM-DD)",
                },
            },
            "required": ["object_id"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    access_token = os.getenv("FACEBOOK_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "FACEBOOK_ACCESS_TOKEN not configured"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if tool_name == "facebook_lead_ads_list_lead_forms":
                params: dict[str, Any] = {
                    "access_token": access_token,
                    "limit": arguments.get("limit", 25),
                }
                if "status" in arguments:
                    params["status"] = arguments["status"]
                r = await client.get(
                    f"{BASE}/{arguments['page_id']}/leadgen_forms",
                    params=params,
                )
                r.raise_for_status()
                data = r.json()
                return {
                    "forms": [
                        {
                            "id": f.get("id"),
                            "name": f.get("name"),
                            "status": f.get("status"),
                            "leads_count": f.get("leads_count"),
                        }
                        for f in data.get("data", [])
                    ],
                    "paging": data.get("paging", {}),
                }

            elif tool_name == "facebook_lead_ads_get_leads":
                params = {
                    "access_token": access_token,
                    "fields": arguments.get("fields", "id,created_time,field_data"),
                    "limit": arguments.get("limit", 50),
                }
                if "after" in arguments:
                    params["after"] = arguments["after"]
                r = await client.get(f"{BASE}/{arguments['form_id']}/leads", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "leads": data.get("data", []),
                    "paging": data.get("paging", {}),
                }

            elif tool_name == "facebook_lead_ads_get_form_details":
                fields = arguments.get("fields", "id,name,status,questions,leads_count,created_time")
                r = await client.get(
                    f"{BASE}/{arguments['form_id']}",
                    params={"access_token": access_token, "fields": fields},
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "facebook_lead_ads_list_ad_accounts":
                params = {
                    "access_token": access_token,
                    "fields": arguments.get("fields", "id,name,account_status,currency"),
                    "limit": arguments.get("limit", 25),
                }
                r = await client.get(f"{BASE}/me/adaccounts", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "ad_accounts": data.get("data", []),
                    "paging": data.get("paging", {}),
                }

            elif tool_name == "facebook_lead_ads_create_custom_audience":
                payload: dict[str, Any] = {
                    "name": arguments["name"],
                    "subtype": arguments.get("subtype", "CUSTOM"),
                    "access_token": access_token,
                }
                if "description" in arguments:
                    payload["description"] = arguments["description"]
                r = await client.post(
                    f"{BASE}/{arguments['ad_account_id']}/customaudiences",
                    data=payload,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "facebook_lead_ads_get_insights":
                params = {
                    "access_token": access_token,
                    "fields": arguments.get("fields", "impressions,reach,spend,leads"),
                }
                if "date_preset" in arguments:
                    params["date_preset"] = arguments["date_preset"]
                if "time_range" in arguments:
                    import json as json_lib
                    params["time_range"] = json_lib.dumps(arguments["time_range"])
                r = await client.get(f"{BASE}/{arguments['object_id']}/insights", params=params)
                r.raise_for_status()
                data = r.json()
                return {
                    "insights": data.get("data", []),
                    "paging": data.get("paging", {}),
                }

            return {"error": f"Unknown tool: {tool_name}"}

        except httpx.HTTPStatusError as exc:
            return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
        except Exception as exc:
            logger.exception("facebook_lead_ads_call_tool_error tool=%s", tool_name)
            return {"error": str(exc)}
