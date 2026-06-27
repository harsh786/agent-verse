"""Google Search Console MCP server — search performance data via Webmaster API.

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

GSC_BASE = "https://searchconsole.googleapis.com/webmasters/v3"

TOOL_DEFINITIONS = [
    {
        "name": "gsc_list_sites",
        "description": "List all sites/properties in Google Search Console",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "gsc_query_search_analytics",
        "description": "Query search performance data (clicks, impressions, CTR, position) with dimension breakdowns",
        "parameters": {
            "type": "object",
            "properties": {
                "site_url": {
                    "type": "string",
                    "description": "Exact site URL as in Search Console (e.g. https://example.com/ or sc-domain:example.com)",
                },
                "start_date": {"type": "string", "description": "YYYY-MM-DD start date"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD end date"},
                "dimensions": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["query", "page", "country", "device", "searchAppearance", "date"],
                    },
                    "default": ["query"],
                    "description": "Breakdown dimensions",
                },
                "row_limit": {"type": "integer", "default": 25},
                "start_row": {"type": "integer", "default": 0},
                "filters": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Optional dimension filter objects",
                },
                "search_type": {
                    "type": "string",
                    "enum": ["web", "image", "video", "news", "googleNews", "discover"],
                    "default": "web",
                },
            },
            "required": ["site_url", "start_date", "end_date"],
        },
    },
    {
        "name": "gsc_list_sitemaps",
        "description": "List submitted sitemaps for a site",
        "parameters": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string"},
            },
            "required": ["site_url"],
        },
    },
    {
        "name": "gsc_submit_sitemap",
        "description": "Submit a sitemap URL to Google Search Console",
        "parameters": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string"},
                "sitemap_url": {"type": "string"},
            },
            "required": ["site_url", "sitemap_url"],
        },
    },
    {
        "name": "gsc_inspect_url",
        "description": "Inspect a URL's indexing status using the URL Inspection API",
        "parameters": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string"},
                "inspection_url": {"type": "string", "description": "URL to inspect"},
            },
            "required": ["site_url", "inspection_url"],
        },
    },
    {
        "name": "gsc_get_top_queries",
        "description": "Get top search queries by clicks for a site over the last 28 days",
        "parameters": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string"},
                "row_limit": {"type": "integer", "default": 20},
                "days": {"type": "integer", "default": 28},
            },
            "required": ["site_url"],
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
                scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
            )
            creds.refresh(Request())
            return creds.token  # type: ignore[return-value]
        except Exception:
            logger.debug("google_service_account_refresh_failed", exc_info=True)
    return ""


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    token = _google_token()
    if not token:
        return {"error": "GOOGLE_ACCESS_TOKEN or GOOGLE_SERVICE_ACCOUNT_JSON required"}

    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            if tool_name == "gsc_list_sites":
                r = await c.get(f"{GSC_BASE}/sites", headers=hdrs)
                r.raise_for_status()
                data = r.json()
                return {
                    "sites": [
                        {"url": s["siteUrl"], "permission_level": s.get("permissionLevel", "")}
                        for s in data.get("siteEntry", [])
                    ]
                }

            elif tool_name == "gsc_query_search_analytics":
                site_url = arguments["site_url"]
                body: dict[str, Any] = {
                    "startDate": arguments["start_date"],
                    "endDate": arguments["end_date"],
                    "dimensions": arguments.get("dimensions", ["query"]),
                    "rowLimit": arguments.get("row_limit", 25),
                    "startRow": arguments.get("start_row", 0),
                    "searchType": arguments.get("search_type", "web"),
                }
                if filters := arguments.get("filters"):
                    body["dimensionFilterGroups"] = [{"filters": filters}]
                import urllib.parse

                encoded = urllib.parse.quote(site_url, safe="")
                r = await c.post(
                    f"{GSC_BASE}/sites/{encoded}/searchAnalytics/query",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "gsc_list_sitemaps":
                site_url = arguments["site_url"]
                import urllib.parse

                encoded = urllib.parse.quote(site_url, safe="")
                r = await c.get(
                    f"{GSC_BASE}/sites/{encoded}/sitemaps",
                    headers=hdrs,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "gsc_submit_sitemap":
                import urllib.parse

                site_url = arguments["site_url"]
                sitemap_url = arguments["sitemap_url"]
                encoded_site = urllib.parse.quote(site_url, safe="")
                encoded_sitemap = urllib.parse.quote(sitemap_url, safe="")
                r = await c.put(
                    f"{GSC_BASE}/sites/{encoded_site}/sitemaps/{encoded_sitemap}",
                    headers=hdrs,
                )
                return {"success": r.status_code in (200, 204)}

            elif tool_name == "gsc_inspect_url":
                # URL Inspection API uses a different endpoint
                inspect_body = {
                    "inspectionUrl": arguments["inspection_url"],
                    "siteUrl": arguments["site_url"],
                }
                r = await c.post(
                    "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
                    headers=hdrs,
                    json=inspect_body,
                )
                r.raise_for_status()
                return r.json()

            elif tool_name == "gsc_get_top_queries":
                import urllib.parse
                from datetime import datetime, timedelta

                days = arguments.get("days", 28)
                end = datetime.utcnow().date()
                start = end - timedelta(days=days)
                site_url = arguments["site_url"]
                encoded = urllib.parse.quote(site_url, safe="")
                body = {
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "dimensions": ["query"],
                    "rowLimit": arguments.get("row_limit", 20),
                    "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
                }
                r = await c.post(
                    f"{GSC_BASE}/sites/{encoded}/searchAnalytics/query",
                    headers=hdrs,
                    json=body,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}

    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"}
    except Exception as exc:
        logger.exception("gsc_call_tool_error tool=%s", tool_name)
        return {"error": str(exc)}
