"""Google Ads MCP server — campaign management via Google Ads API.

Environment variables:
  GOOGLE_ACCESS_TOKEN:         OAuth2 bearer token with Ads scope
  GOOGLE_ADS_DEVELOPER_TOKEN:  Developer token (required for Ads API)
  GOOGLE_ADS_CUSTOMER_ID:      Default customer/account ID (without dashes)
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

ADS_BASE = "https://googleads.googleapis.com/v17"

TOOL_DEFINITIONS = [
    {
        "name": "gads_search_campaigns",
        "description": "Search Google Ads campaigns for a customer account using GAQL",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "Google Ads customer/account ID (no dashes)",
                },
                "query": {
                    "type": "string",
                    "description": "GAQL query string, e.g. SELECT campaign.id, campaign.name FROM campaign WHERE campaign.status = 'ENABLED'",
                },
            },
            "required": ["customer_id", "query"],
        },
    },
    {
        "name": "gads_list_campaigns",
        "description": "List all campaigns for a Google Ads customer",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "status_filter": {
                    "type": "string",
                    "enum": ["ENABLED", "PAUSED", "REMOVED", "ALL"],
                    "default": "ENABLED",
                },
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "gads_get_campaign_performance",
        "description": "Get performance metrics for campaigns (impressions, clicks, cost, conversions)",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "date_range": {
                    "type": "string",
                    "enum": ["TODAY", "YESTERDAY", "LAST_7_DAYS", "LAST_30_DAYS", "THIS_MONTH", "LAST_MONTH"],
                    "default": "LAST_30_DAYS",
                },
                "campaign_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of campaign IDs to filter on",
                },
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "gads_create_campaign",
        "description": "Create a new Google Ads campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "name": {"type": "string"},
                "advertising_channel_type": {
                    "type": "string",
                    "enum": ["SEARCH", "DISPLAY", "SHOPPING", "VIDEO", "PERFORMANCE_MAX"],
                    "default": "SEARCH",
                },
                "bidding_strategy_type": {
                    "type": "string",
                    "enum": ["MANUAL_CPC", "TARGET_CPA", "TARGET_ROAS", "MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS"],
                    "default": "MANUAL_CPC",
                },
                "budget_amount_micros": {
                    "type": "integer",
                    "description": "Daily budget in micros (1 USD = 1,000,000)",
                },
                "status": {"type": "string", "enum": ["ENABLED", "PAUSED"], "default": "PAUSED"},
            },
            "required": ["customer_id", "name", "budget_amount_micros"],
        },
    },
    {
        "name": "gads_pause_campaign",
        "description": "Pause a Google Ads campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "campaign_id": {"type": "string"},
            },
            "required": ["customer_id", "campaign_id"],
        },
    },
    {
        "name": "gads_get_keywords",
        "description": "List ad group keywords for a campaign",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "campaign_id": {"type": "string"},
                "status_filter": {"type": "string", "default": "ENABLED"},
            },
            "required": ["customer_id", "campaign_id"],
        },
    },
    {
        "name": "gads_get_account_summary",
        "description": "Get a high-level summary of spend and performance for a Google Ads account",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "date_range": {
                    "type": "string",
                    "enum": ["LAST_7_DAYS", "LAST_30_DAYS", "THIS_MONTH", "LAST_MONTH"],
                    "default": "LAST_30_DAYS",
                },
            },
            "required": ["customer_id"],
        },
    },
]


def _headers() -> dict[str, str]:
    token = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    dev_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "developer-token": dev_token,
        "Content-Type": "application/json",
    }


async def _gaql_search(
    c: httpx.AsyncClient, customer_id: str, query: str
) -> dict[str, Any]:
    r = await c.post(
        f"{ADS_BASE}/customers/{customer_id}/googleAds:search",
        headers=_headers(),
        json={"query": query},
    )
    r.raise_for_status()
    return r.json()


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    dev_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    if not token or not dev_token:
        return {"error": "GOOGLE_ACCESS_TOKEN and GOOGLE_ADS_DEVELOPER_TOKEN required"}

    cid = arguments.get("customer_id", os.getenv("GOOGLE_ADS_CUSTOMER_ID", ""))
    if not cid:
        return {"error": "customer_id argument or GOOGLE_ADS_CUSTOMER_ID env var required"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "gads_search_campaigns":
                return await _gaql_search(c, arguments["customer_id"], arguments["query"])

            elif tool_name == "gads_list_campaigns":
                status = arguments.get("status_filter", "ENABLED")
                where = "" if status == "ALL" else f"WHERE campaign.status = '{status}'"
                query = f"SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type FROM campaign {where}"
                return await _gaql_search(c, cid, query)

            elif tool_name == "gads_get_campaign_performance":
                dr = arguments.get("date_range", "LAST_30_DAYS")
                campaign_ids = arguments.get("campaign_ids", [])
                extra = ""
                if campaign_ids:
                    ids_str = ", ".join(f"'{i}'" for i in campaign_ids)
                    extra = f"AND campaign.id IN ({ids_str})"
                query = (
                    f"SELECT campaign.id, campaign.name, "
                    f"metrics.impressions, metrics.clicks, metrics.cost_micros, "
                    f"metrics.conversions, metrics.ctr "
                    f"FROM campaign "
                    f"WHERE segments.date DURING {dr} {extra}"
                )
                return await _gaql_search(c, cid, query)

            elif tool_name == "gads_create_campaign":
                # Requires mutate operation
                body = {
                    "operations": [
                        {
                            "create": {
                                "name": arguments["name"],
                                "status": arguments.get("status", "PAUSED"),
                                "advertisingChannelType": arguments.get(
                                    "advertising_channel_type", "SEARCH"
                                ),
                                "campaignBudget": {
                                    "amountMicros": str(arguments["budget_amount_micros"]),
                                    "deliveryMethod": "STANDARD",
                                },
                                "biddingStrategyType": arguments.get(
                                    "bidding_strategy_type", "MANUAL_CPC"
                                ),
                            }
                        }
                    ]
                }
                r = await c.post(
                    f"{ADS_BASE}/customers/{cid}/campaigns:mutate",
                    headers=_headers(),
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "gads_pause_campaign":
                campaign_id = arguments["campaign_id"]
                body = {
                    "operations": [
                        {
                            "update": {
                                "resourceName": f"customers/{cid}/campaigns/{campaign_id}",
                                "status": "PAUSED",
                            },
                            "updateMask": "status",
                        }
                    ]
                }
                r = await c.post(
                    f"{ADS_BASE}/customers/{cid}/campaigns:mutate",
                    headers=_headers(),
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "gads_get_keywords":
                campaign_id = arguments["campaign_id"]
                status = arguments.get("status_filter", "ENABLED")
                where_parts = [f"campaign.id = {campaign_id}"]
                if status != "ALL":
                    where_parts.append(f"ad_group_criterion.status = '{status}'")
                where = " AND ".join(where_parts)
                query = (
                    f"SELECT ad_group_criterion.keyword.text, "
                    f"ad_group_criterion.keyword.match_type, "
                    f"ad_group_criterion.status, ad_group.name "
                    f"FROM keyword_view WHERE {where}"
                )
                return await _gaql_search(c, cid, query)

            elif tool_name == "gads_get_account_summary":
                dr = arguments.get("date_range", "LAST_30_DAYS")
                query = (
                    f"SELECT customer.id, customer.descriptive_name, "
                    f"metrics.impressions, metrics.clicks, metrics.cost_micros, "
                    f"metrics.conversions "
                    f"FROM customer WHERE segments.date DURING {dr}"
                )
                return await _gaql_search(c, cid, query)

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("gads_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
