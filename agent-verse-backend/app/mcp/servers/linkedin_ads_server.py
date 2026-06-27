"""LinkedIn Ads MCP server — ad accounts, campaigns, creatives, and analytics.

Environment variables:
  LINKEDIN_ACCESS_TOKEN: OAuth2 access token with r_ads / rw_ads scopes
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

LINKEDIN_BASE = "https://api.linkedin.com/v2"

TOOL_DEFINITIONS = [
    {
        "name": "linkedin_ads_list_accounts",
        "description": "List LinkedIn Ad accounts accessible to the authenticated user",
        "parameters": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 10},
                "start": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "linkedin_ads_list_campaigns",
        "description": "List campaigns for a LinkedIn Ad account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Ad account ID (numeric, without urn prefix)",
                },
                "status": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by status: ACTIVE, PAUSED, ARCHIVED, etc.",
                },
                "count": {"type": "integer", "default": 20},
                "start": {"type": "integer", "default": 0},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "linkedin_ads_get_campaign_analytics",
        "description": "Get performance analytics for a campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
                "date_range_start": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD",
                },
                "date_range_end": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD",
                },
                "time_granularity": {
                    "type": "string",
                    "enum": ["ALL", "DAILY", "MONTHLY", "YEARLY"],
                    "default": "ALL",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Metrics: impressions, clicks, spend, conversions, etc.",
                },
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "linkedin_ads_list_creatives",
        "description": "List ad creatives for a campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
                "count": {"type": "integer", "default": 20},
                "start": {"type": "integer", "default": 0},
            },
            "required": ["campaign_id"],
        },
    },
    {
        "name": "linkedin_ads_create_campaign",
        "description": "Create a new LinkedIn Ads campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "name": {"type": "string"},
                "campaign_group_id": {"type": "string"},
                "objective_type": {
                    "type": "string",
                    "enum": [
                        "BRAND_AWARENESS",
                        "WEBSITE_VISITS",
                        "ENGAGEMENT",
                        "VIDEO_VIEWS",
                        "LEAD_GENERATION",
                        "WEBSITE_CONVERSIONS",
                        "JOB_APPLICANTS",
                    ],
                    "default": "WEBSITE_VISITS",
                },
                "daily_budget_amount": {"type": "number"},
                "daily_budget_currency": {"type": "string", "default": "USD"},
            },
            "required": ["account_id", "name"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "LinkedIn-Version": "202401",
        "X-Restli-Protocol-Version": "2.0.0",
    }


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
    if not token:
        return {"error": "LINKEDIN_ACCESS_TOKEN not configured"}

    try:
        async with httpx.AsyncClient(
            base_url=LINKEDIN_BASE, headers=_headers(), timeout=30.0
        ) as c:
            if tool_name == "linkedin_ads_list_accounts":
                params: dict[str, Any] = {
                    "count": arguments.get("count", 10),
                    "start": arguments.get("start", 0),
                    "q": "search",
                }
                r = await c.get("/adAccountsV2", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "linkedin_ads_list_campaigns":
                account_id = arguments["account_id"]
                params = {
                    "q": "search",
                    "search.account.values[0]": f"urn:li:sponsoredAccount:{account_id}",
                    "count": arguments.get("count", 20),
                    "start": arguments.get("start", 0),
                }
                if statuses := arguments.get("status"):
                    for i, s in enumerate(statuses):
                        params[f"search.status.values[{i}]"] = s
                r = await c.get("/adCampaignsV2", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "linkedin_ads_get_campaign_analytics":
                cid = arguments["campaign_id"]
                params = {
                    "q": "analytics",
                    "pivot": "CAMPAIGN",
                    "timeGranularity": arguments.get("time_granularity", "ALL"),
                    "campaigns[0]": f"urn:li:sponsoredCampaign:{cid}",
                }
                if ds := arguments.get("date_range_start"):
                    y, m, d = ds.split("-")
                    params["dateRange.start.year"] = y
                    params["dateRange.start.month"] = m
                    params["dateRange.start.day"] = d
                if de := arguments.get("date_range_end"):
                    y, m, d = de.split("-")
                    params["dateRange.end.year"] = y
                    params["dateRange.end.month"] = m
                    params["dateRange.end.day"] = d
                if fields := arguments.get("fields"):
                    params["fields"] = ",".join(fields)
                r = await c.get("/adAnalyticsV2", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "linkedin_ads_list_creatives":
                cid = arguments["campaign_id"]
                params = {
                    "q": "search",
                    "search.campaign.values[0]": f"urn:li:sponsoredCampaign:{cid}",
                    "count": arguments.get("count", 20),
                    "start": arguments.get("start", 0),
                }
                r = await c.get("/adCreativesV2", params=params)
                r.raise_for_status()
                return r.json()

            elif tool_name == "linkedin_ads_create_campaign":
                account_id = arguments["account_id"]
                payload: dict[str, Any] = {
                    "account": f"urn:li:sponsoredAccount:{account_id}",
                    "name": arguments["name"],
                    "objectiveType": arguments.get("objective_type", "WEBSITE_VISITS"),
                    "status": "DRAFT",
                    "type": "TEXT_AD",
                }
                if cg := arguments.get("campaign_group_id"):
                    payload["campaignGroup"] = f"urn:li:sponsoredCampaignGroup:{cg}"
                if budget := arguments.get("daily_budget_amount"):
                    payload["dailyBudget"] = {
                        "amount": str(budget),
                        "currencyCode": arguments.get("daily_budget_currency", "USD"),
                    }
                r = await c.post("/adCampaignsV2", json=payload)
                r.raise_for_status()
                return {"campaign_urn": r.headers.get("x-restli-id"), "status": "DRAFT"}

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("linkedin_ads_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
