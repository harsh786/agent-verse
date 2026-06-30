"""Google My Business MCP server — manage business locations, reviews, and insights.

Environment:
  GOOGLE_ACCESS_TOKEN: OAuth2 access token with business.manage scope
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://mybusinessaccountmanagement.googleapis.com/v1"
BUSINESS_INFO_URL = "https://mybusinessbusinessinformation.googleapis.com/v1"
REVIEWS_URL = "https://mybusiness.googleapis.com/v4"

TOOL_DEFINITIONS = [
    {
        "name": "google_mybusiness_list_locations",
        "description": "List all business locations associated with the account",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Google My Business account ID"},
                "page_size": {"type": "integer", "description": "Locations per page (max 100)"},
                "page_token": {"type": "string", "description": "Pagination token"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "google_mybusiness_get_location",
        "description": "Get detailed information for a specific business location",
        "parameters": {
            "type": "object",
            "properties": {
                "location_name": {"type": "string", "description": "Location resource name (locations/*)"},
                "read_mask": {"type": "string", "description": "Fields to return (e.g. name,title,websiteUri)"},
            },
            "required": ["location_name"],
        },
    },
    {
        "name": "google_mybusiness_update_location",
        "description": "Update business location attributes such as hours, description, or contact info",
        "parameters": {
            "type": "object",
            "properties": {
                "location_name": {"type": "string", "description": "Location resource name"},
                "title": {"type": "string", "description": "Business name"},
                "website_uri": {"type": "string", "description": "Business website URL"},
                "phone_numbers": {"type": "object", "description": "Phone number details"},
                "update_mask": {"type": "string", "description": "Comma-separated fields to update"},
            },
            "required": ["location_name"],
        },
    },
    {
        "name": "google_mybusiness_list_reviews",
        "description": "List customer reviews for a business location",
        "parameters": {
            "type": "object",
            "properties": {
                "location_name": {"type": "string", "description": "Location resource name"},
                "page_size": {"type": "integer", "description": "Reviews per page"},
                "page_token": {"type": "string", "description": "Pagination token"},
                "order_by": {"type": "string", "description": "Sort order (updateTime desc)"},
            },
            "required": ["location_name"],
        },
    },
    {
        "name": "google_mybusiness_reply_to_review",
        "description": "Post an owner reply to a customer review",
        "parameters": {
            "type": "object",
            "properties": {
                "review_name": {"type": "string", "description": "Review resource name (locations/*/reviews/*)"},
                "comment": {"type": "string", "description": "Reply text to post as the business owner"},
            },
            "required": ["review_name", "comment"],
        },
    },
    {
        "name": "google_mybusiness_get_insights",
        "description": "Get performance insights and metrics for a business location",
        "parameters": {
            "type": "object",
            "properties": {
                "location_name": {"type": "string", "description": "Location resource name"},
                "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                "metric_requests": {
                    "type": "array",
                    "description": "List of metric types to retrieve",
                    "items": {"type": "string"},
                },
            },
            "required": ["location_name"],
        },
    },
]


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    access_token = os.getenv("GOOGLE_ACCESS_TOKEN", "")
    if not access_token:
        return {"error": "GOOGLE_ACCESS_TOKEN not configured"}

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "google_mybusiness_list_locations":
                account_id = arguments["account_id"]
                params: dict[str, Any] = {}
                if "page_size" in arguments:
                    params["pageSize"] = arguments["page_size"]
                if "page_token" in arguments:
                    params["pageToken"] = arguments["page_token"]
                r = await client.get(
                    f"{BASE_URL}/accounts/{account_id}/locations",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_mybusiness_get_location":
                params = {"readMask": arguments.get("read_mask", "name,title,websiteUri,phoneNumbers")}
                r = await client.get(
                    f"{BUSINESS_INFO_URL}/{arguments['location_name']}",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_mybusiness_update_location":
                location_name = arguments["location_name"]
                payload: dict[str, Any] = {}
                update_fields = []
                if "title" in arguments:
                    payload["title"] = arguments["title"]
                    update_fields.append("title")
                if "website_uri" in arguments:
                    payload["websiteUri"] = arguments["website_uri"]
                    update_fields.append("websiteUri")
                if "phone_numbers" in arguments:
                    payload["phoneNumbers"] = arguments["phone_numbers"]
                    update_fields.append("phoneNumbers")
                mask = arguments.get("update_mask", ",".join(update_fields))
                r = await client.patch(
                    f"{BUSINESS_INFO_URL}/{location_name}",
                    headers=headers,
                    params={"updateMask": mask},
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_mybusiness_list_reviews":
                loc = arguments["location_name"]
                params = {}
                if "page_size" in arguments:
                    params["pageSize"] = arguments["page_size"]
                if "page_token" in arguments:
                    params["pageToken"] = arguments["page_token"]
                if "order_by" in arguments:
                    params["orderBy"] = arguments["order_by"]
                r = await client.get(
                    f"{REVIEWS_URL}/{loc}/reviews",
                    headers=headers,
                    params=params,
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_mybusiness_reply_to_review":
                r = await client.put(
                    f"{REVIEWS_URL}/{arguments['review_name']}/reply",
                    headers=headers,
                    json={"comment": arguments["comment"]},
                )
                r.raise_for_status()
                return r.json()

            if tool_name == "google_mybusiness_get_insights":
                loc = arguments["location_name"]
                payload = {
                    "locationNames": [loc],
                    "basicRequest": {
                        "metricRequests": [
                            {"metric": m} for m in arguments.get("metric_requests", ["QUERIES_DIRECT"])
                        ],
                        "timeRange": {
                            "startTime": arguments.get("start_date", "2024-01-01") + "T00:00:00Z",
                            "endTime": arguments.get("end_date", "2024-12-31") + "T00:00:00Z",
                        },
                    },
                }
                r = await client.post(
                    f"{REVIEWS_URL}/accounts/-/locations:reportInsights",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
